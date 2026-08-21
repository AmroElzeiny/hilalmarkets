"""One header, on every email the product sends.

The header used to spend the most valuable space in the message on three things nobody
needed: the brand name typed out as text, a strapline saying what the product is, and a
green shape in the corner that meant nothing at all. It is now the real logo on the
left and, on the right, the *kind* of message this is.

The rules below are about the class, not about the one file that draws it:

* **The logo is never redrawn.** It is built from the site's own
  ``hilal-markets-logo.svg`` by ``scripts/build_email_logo.py``. A second drawing of a
  logo is how a product ends up with two logos.
* **There is one frame.** ``email_branding`` owns it. A template that writes its own
  ``<html>`` is a second frame, and a second frame drifts — the receipt already had
  one, with its own colours, its own footer and its own typeface.
* **The corner says something.** Either it names the kind of message, or it is empty.
  It is never decoration.
* **A blocked picture still reads.** Outlook blocks images by default, so the `alt`
  text is styled to look like the wordmark instead of vanishing into the dark header.

Every check is parametrised across every email the product can render, so an email
written next month is covered the day it is written.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models.enums import AlertType
from ai_market_monitor.services.alert_emails import AlertEmailRenderer
from ai_market_monitor.services.alert_presentation import AlertPresentation
from ai_market_monitor.services.email_branding import (
    APPLE,
    EMAIL_LOGO_HEIGHT,
    EMAIL_LOGO_PATH,
    EMAIL_LOGO_WIDTH,
    INK,
    INK_RAISED,
    MESSAGE_KIND_BY_PURPOSE,
    SURFACE,
    HilalMarketsEmailRenderer,
    message_kind_label,
    plain_text_block,
)
from ai_market_monitor.services.payment_emails import PaymentEmailRenderer
from ai_market_monitor.services.product_language import (
    UNKNOWN_MESSAGE_KIND,
    every_message_kind,
)
from tests.support.contrast import contrast

BASE_URL = "https://hilalmarkets.com"
STATIC = Path("src/ai_market_monitor/static")
LOGO_FILE = STATIC / EMAIL_LOGO_PATH.removeprefix("/static/")
SITE_LOGO = STATIC / "hilal-markets-logo.svg"
EMAIL_TEMPLATES = Path("src/ai_market_monitor/templates/email")

#: The words that used to sit in the header. Gone, and they must stay gone.
RETIRED_HEADER_TEXT = (
    ">hilal markets<",
    "Evidence-led crypto monitoring",
)


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "database_url": "sqlite+aiosqlite://",
        "email_adapter": "memory",
        "public_base_url": BASE_URL,
    }
    values.update(overrides)
    return Settings(**values)


class _Alert:
    """Only the fields `AlertPresentation.from_alert` reads."""

    def __init__(self, alert_type: AlertType):
        self.id = uuid4()
        self.user_id = uuid4()
        self.alert_type = alert_type
        self.title = "Something happened"
        self.body = "A sample body."
        self.proof_receipt = {
            "symbol": "SMPL/USDT",
            "direction": "long",
            "strategy_name": "Steady movers",
            "strategy_version": "1",
            "exchange": "binance",
            "timeframe": "4h",
            "setup_completion_score": 100,
            "setup_state": "confirmed",
            "conditions": [],
        }
        self.created_at = datetime(2026, 8, 18, 10, 30, tzinfo=UTC)
        self.strategy_version_id = None
        self.setup_instance_id = None
        self.chart_snapshot_url = None


def _every_email() -> dict[str, str]:
    """Every email the product can render, keyed by a name a person recognises.

    Built from the renderers themselves rather than from a written list, because a
    written list is a thing somebody has to remember to add to.
    """

    settings = _settings()
    frame = HilalMarketsEmailRenderer(settings)
    emails: dict[str, str] = {}

    for purpose in ("login", "password_reset", "signup", "system_brain", "unknown"):
        emails[f"code-{purpose}"] = frame.auth_code(
            recipient="someone@example.com", code="123456", purpose=purpose, ttl_minutes=10
        ).html_body
    emails["signup-welcome"] = frame.signup_welcome(
        first_name="Amr", email="someone@example.com"
    ).html_body
    emails["connection-test"] = frame.connection_test().html_body
    emails["access-changed"] = frame.access_changed(
        first_name="Amr",
        plan_name="Pro",
        duration_label="12 months",
        ends_at_label="18 Aug 2027",
    ).html_body

    alerts = AlertEmailRenderer(settings)
    for alert_type in AlertType:
        presentation = AlertPresentation.from_alert(
            _Alert(alert_type), public_base_url=BASE_URL
        )
        emails[f"alert-{alert_type.value}"] = alerts.render(presentation).html_body

    emails["payment-success"] = (
        PaymentEmailRenderer(settings)
        .render(
            first_name="Amr",
            plan_name="Pro",
            billing_frequency="monthly",
            amount=Decimal("29.00"),
            currency="USD",
            payment_date=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
            period_end_date=datetime(2026, 9, 17, 12, 0, tzinfo=UTC),
            renews_automatically=True,
            receipt_url=None,
            plan_limits={"active_strategies": 10},
        )
        .html_body
    )

    # The bare-body path: a message composed somewhere else, framed on the way out.
    for purpose in sorted(MESSAGE_KIND_BY_PURPOSE):
        emails[f"wrapped-{purpose}"] = frame.ensure_shell(
            subject="A message",
            html_body=plain_text_block("Assalamu Alaikum."),
            eyebrow=message_kind_label(purpose),
        )
    return emails


EMAILS = _every_email()
EMAIL_IDS = sorted(EMAILS)


def test_the_family_is_not_empty_and_covers_every_renderer():
    """A rule that matches nothing passes for the wrong reason."""

    assert len(EMAILS) >= 20
    assert {"payment-success", "signup-welcome", "connection-test"} <= set(EMAILS)
    assert any(name.startswith("alert-") for name in EMAILS)
    assert any(name.startswith("code-") for name in EMAILS)


# ── What the header no longer says ───────────────────────────────────────────


@pytest.mark.parametrize("name", EMAIL_IDS)
@pytest.mark.parametrize("retired", RETIRED_HEADER_TEXT)
def test_no_email_still_carries_the_old_header_text(name: str, retired: str):
    assert retired not in EMAILS[name], (
        f"{name} still shows {retired!r} in its header. The header is the logo and the "
        "kind of message, and nothing else."
    )


@pytest.mark.parametrize("name", EMAIL_IDS)
def test_no_email_draws_a_shape_by_hand(name: str):
    """The green chamfer was a `clip-path` polygon. No email draws one again.

    `clip-path` is also the one property that quietly disappears in the Outlook desktop
    clients, so the shape half the readers saw was not the shape the design described.
    """

    assert "clip-path" not in EMAILS[name]
    assert "polygon(" not in EMAILS[name]


# ── The logo ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", EMAIL_IDS)
def test_every_email_shows_the_white_logo_once(name: str):
    html = EMAILS[name]
    assert html.count("<img") == 1, (
        f"{name} has {html.count('<img')} pictures. The logo is the only one: every "
        "other thing an email says has to survive pictures being switched off."
    )
    assert f'src="{BASE_URL}{EMAIL_LOGO_PATH}"' in html
    assert f'width="{EMAIL_LOGO_WIDTH}" height="{EMAIL_LOGO_HEIGHT}"' in html


@pytest.mark.parametrize("name", EMAIL_IDS)
def test_a_blocked_logo_still_says_the_brand_name(name: str):
    """Outlook blocks pictures by default. The header must not go blank when it does."""

    header = EMAILS[name].split("<img", 1)[1].split(">", 1)[0]
    assert 'alt="Hilal Markets"' in header
    # The alt text inherits the img's own colour. Unstyled it would be near-black on a
    # near-black header, which is the same as no header at all.
    assert f"color:{SURFACE}" in header
    assert "font-family:Geometria" in header


def test_the_logo_file_exists_and_was_built_from_the_site_logo():
    """Not redrawn: the same shape as the mark the website itself shows."""

    assert LOGO_FILE.is_file(), (
        f"{LOGO_FILE} is missing. Run: .venv/Scripts/python scripts/build_email_logo.py"
    )
    view_box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', SITE_LOGO.read_text("utf-8"))
    assert view_box is not None
    site_ratio = float(view_box.group(1)) / float(view_box.group(2))
    header_ratio = EMAIL_LOGO_WIDTH / EMAIL_LOGO_HEIGHT
    assert abs(site_ratio - header_ratio) < 0.05, (
        "The header shows the site logo at a different shape from the site's own. "
        "Change the size in scripts/build_email_logo.py, not the picture."
    )


def test_the_logo_is_a_png_because_no_mail_client_draws_an_svg():
    assert EMAIL_LOGO_PATH.endswith(".png")
    assert LOGO_FILE.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# ── The corner ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", EMAIL_IDS)
def test_the_corner_either_names_the_message_or_is_empty(name: str):
    """Never decoration. The one thing in that corner is a word a reader can use."""

    html = EMAILS[name]
    header = html.split('<tr><td style="padding:24px 30px 30px', 1)[1].split("</tr>", 1)[0]
    if INK_RAISED in header:
        chip = header.split(INK_RAISED, 1)[1]
        words = re.sub(r"<[^>]+>", "", chip.split("</td>", 1)[0]).strip()
        assert words, f"{name} has an empty chip in the header corner."
    else:
        assert "&nbsp;" in header


@pytest.mark.parametrize("kind", list(every_message_kind()), ids=lambda kind: kind.icon)
def test_a_message_kind_never_repeats_its_own_label_in_the_chip(kind):
    """The chip is the family, not the headline.

    "Nearly there" is what happened to one coin. Printed in the header as well as in
    the title and in the status band, the same short sentence appears three times on
    one screen, and none of the three tells the reader anything the others did not.
    """

    assert kind.category
    assert kind.category != kind.label
    assert len(kind.category) <= 20, "A chip is two or three words, not a sentence."


def test_an_unnamed_message_kind_gets_a_plain_category_not_a_guess():
    assert UNKNOWN_MESSAGE_KIND.category == "Notice"


@pytest.mark.parametrize("purpose", sorted(MESSAGE_KIND_BY_PURPOSE))
def test_every_delivery_purpose_names_its_chip_in_short_plain_words(purpose: str):
    label = message_kind_label(purpose)
    assert label
    assert len(label) <= 20
    assert label == label.strip()


def test_an_unknown_purpose_gets_no_chip_rather_than_an_invented_one():
    """Fail closed. A made-up category is a claim about a message nobody checked."""

    assert message_kind_label("something_new") is None
    assert message_kind_label(None) is None
    assert message_kind_label("") is None


def test_the_chip_colours_are_readable_on_the_header():
    """Measured, not assumed. Apple green is invisible on white and fine on near-black."""

    assert contrast(APPLE, INK_RAISED) >= 4.5
    assert contrast(SURFACE, INK) >= 4.5


# ── One frame ────────────────────────────────────────────────────────────────


def test_no_email_template_writes_a_frame_of_its_own():
    """`email_branding` owns the frame. The receipt used to own a second one.

    That second frame is exactly the failure this codebase keeps repeating: the same
    decision made in two places, drifting. Its header had already stopped matching, and
    its typeface stack asked for Arial where the shared one asks for Onest first.
    """

    for path in sorted(EMAIL_TEMPLATES.rglob("*")):
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8").lower()
        assert "<html" not in body and "<!doctype" not in body, (
            f"{path} builds an email frame of its own. Compose the blocks in "
            "email_branding instead, and let it draw the frame."
        )


@pytest.mark.parametrize("name", EMAIL_IDS)
def test_every_email_really_is_the_shared_frame(name: str):
    html = EMAILS[name]
    assert 'data-hm-email-shell="true"' in html
    assert html.lower().startswith("<!doctype html>")
    assert html.count("<!doctype") == 1


