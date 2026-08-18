"""Email as a real delivery channel, and the emails it sends.

Two families of check, because there are two ways this can be wrong:

* **The channel.** It must be offered only when it can deliver, go only to a verified
  address, be recorded like every other channel, and retry the same way. A channel that
  is offered but never delivers is worse than one that is honestly missing, because the
  silence looks like "there was nothing to tell you".
* **The words.** An email must never say something the same alert would not say on
  Telegram, must never conclude a Shariah status, must never carry a forbidden claim,
  and must read for somebody who is not an engineer.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models.enums import AlertType, DeliveryChannel
from ai_market_monitor.services.alert_emails import AlertEmailRenderer
from ai_market_monitor.services.alert_presentation import AlertPresentation
from ai_market_monitor.services.email_delivery import email_delivery_available
from ai_market_monitor.services.notification_preferences import offered_channels
from ai_market_monitor.services.product_language import every_message_kind, message_kind


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "database_url": "sqlite+aiosqlite://",
        "email_adapter": "memory",
        "public_base_url": "https://hilalmarkets.com",
    }
    values.update(overrides)
    return Settings(**values)


class _Alert:
    """The fields `AlertPresentation.from_alert` reads. Nothing else is needed here."""

    def __init__(self, alert_type, title, body, proof):
        self.id = uuid4()
        self.user_id = uuid4()
        self.alert_type = alert_type
        self.title = title
        self.body = body
        self.proof_receipt = proof
        self.created_at = datetime(2026, 8, 16, 10, 30, tzinfo=UTC)
        self.strategy_version_id = None
        self.setup_instance_id = None
        self.chart_snapshot_url = None


_SETUP_PROOF = {
    "symbol": "SOL/USDT",
    "direction": "long",
    "strategy_name": "Steady majors",
    "strategy_version": "4",
    "exchange": "binance",
    "timeframe": "4h",
    "setup_completion_score": 100,
    "setup_state": "confirmed",
    "data_latency_ms": 420,
    "market_data_timestamp": "2026-08-16T09:58:00+00:00",
    "alert_trust_score": {"score": 92, "grade": "high", "factors": []},
    "conditions": [
        {"name": "RSI below 35", "state": "passed", "actual_value": "31.4",
         "required_value": "35"},
        {"name": "Volume twice its usual", "state": "monitoring", "actual_value": None,
         "required_value": "2x"},
    ],
    "sharia_screening": {
        "asset": {"canonical_asset": "SOL", "status": "eligible", "reviewed_at": "2026-07-30"},
        "methodology_code": "AAOIFI",
        "methodology_version": "2.1",
    },
}

_ALERTS = {
    AlertType.CONFIRMED: _Alert(AlertType.CONFIRMED, "Match", "Everything matched.", _SETUP_PROOF),
    AlertType.NEAR_MISS: _Alert(AlertType.NEAR_MISS, "Close", "Nearly.", _SETUP_PROOF),
    AlertType.FORMING: _Alert(AlertType.FORMING, "Forming", "Starting.", _SETUP_PROOF),
    AlertType.LIFECYCLE: _Alert(AlertType.LIFECYCLE, "Moved", "It moved.", _SETUP_PROOF),
    AlertType.FAILURE: _Alert(AlertType.FAILURE, "No data", "Could not read.", _SETUP_PROOF),
    AlertType.COMPLIANCE: _Alert(
        AlertType.COMPLIANCE,
        "SOL screening status changed to under review",
        "The reviewed record for SOL moved to under review on 16 August 2026.",
        {
            "canonical_asset": "SOL",
            "new_status": "under_review",
            "reviewed_at": "2026-08-16",
            "methodology_version": "2.1",
        },
    ),
    AlertType.TRIAL: _Alert(
        AlertType.TRIAL,
        "Your trial ends in three days",
        "Your trial ends on 19 August 2026.",
        {"trial_status": "ending_soon"},
    ),
}


def _rendered(alert_type: AlertType, **settings_overrides):
    settings = _settings(**settings_overrides)
    presentation = AlertPresentation.from_alert(
        _ALERTS[alert_type], public_base_url="https://hilalmarkets.com"
    )
    return AlertEmailRenderer(settings).render(presentation)


# ── The channel ──────────────────────────────────────────────────────────────


def test_email_is_offered_when_a_sender_is_configured():
    assert DeliveryChannel.EMAIL in offered_channels(_settings(email_adapter="memory"))
    assert DeliveryChannel.EMAIL in offered_channels(
        _settings(email_adapter="smtp", smtp_host="smtp.example", smtp_from_email="a@b.c")
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"email_adapter": "none"},
        # Configured as SMTP but with nothing to send through: still not deliverable.
        {"email_adapter": "smtp"},
        {"email_adapter": "smtp", "smtp_host": "smtp.example"},
        {"email_adapter": "smtp", "smtp_from_email": "a@b.c"},
    ],
)
def test_email_is_not_offered_when_nothing_could_send_it(overrides):
    """A channel a person can switch on but that nothing delivers is a promise the
    product cannot keep. The silence afterwards looks like "nothing happened"."""

    settings = _settings(**overrides)
    assert not email_delivery_available(settings)
    assert DeliveryChannel.EMAIL not in offered_channels(settings)


def test_the_retired_channel_is_still_never_offered():
    """`DeliveryChannel` keeps `discord` so historical rows stay readable. Adding email
    must not turn "every value of the enum" back into "every channel we offer"."""

    assert DeliveryChannel.DISCORD not in offered_channels(_settings())


# ── What an alert email says ─────────────────────────────────────────────────


@pytest.mark.parametrize("alert_type", list(_ALERTS))
def test_every_kind_of_alert_produces_a_complete_email(alert_type: AlertType):
    rendered = _rendered(alert_type)

    assert rendered.subject.strip()
    assert rendered.text_body.strip(), "an email with no plain-text part"
    assert "<!doctype html>" in rendered.html_body.lower()
    assert "hilal markets" in rendered.html_body.lower()


@pytest.mark.parametrize("alert_type", list(_ALERTS))
def test_the_subject_names_the_kind_in_plain_words(alert_type: AlertType):
    """The same words the Connections page uses when it promises this message."""

    rendered = _rendered(alert_type)
    assert message_kind(alert_type.value).label in rendered.subject


@pytest.mark.parametrize("alert_type", list(_ALERTS))
@pytest.mark.parametrize(
    "claim", ["100% halal", "guaranteed", "risk-free", "buy now", "sell now", "AI trades for you"]
)
def test_no_forbidden_claim_reaches_an_email(alert_type: AlertType, claim: str):
    rendered = _rendered(alert_type)
    assert claim.lower() not in rendered.text_body.lower()
    assert claim.lower() not in rendered.html_body.lower()


#: What counts as an emoji, for the rule that says an email may not carry one.
#:
#: The pictograph planes, the older Miscellaneous Symbols block, and the variation
#: selector that turns an ordinary character into a coloured picture.
#:
#: Dingbats (U+2700–U+27BF) is deliberately *not* here. It holds ✓ and ✗, which are
#: typographic marks — they render as text in the reader's own colour, at the reader's
#: own size, and they are what this design uses to pair a word with a mark so a status
#: never rests on colour alone. Banning them would ban the accessible thing.
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-⛿️]")


@pytest.mark.parametrize("alert_type", list(_ALERTS))
def test_no_emoji_reaches_an_email(alert_type: AlertType):
    """`brand guide.md` rules out pictographs. They used to arrive through the alert's
    own button labels, which were written for a chat window."""

    rendered = _rendered(alert_type)
    found = _EMOJI.findall(rendered.text_body + rendered.html_body)
    assert not found, f"an emoji reached an email: {found}"


#: A web address inside the message. Matched so it can be taken out before the prose is
#: read, never to check the address itself.
_URL_IN_TEXT = re.compile(r"\bhttps?://\S+|\b\S*/\S+", re.IGNORECASE)


@pytest.mark.parametrize("alert_type", list(_ALERTS))
@pytest.mark.parametrize("word", ["lifecycle", "n/a", "proof_receipt", "near_miss", "[PASS]"])
def test_no_word_from_inside_the_machine_reaches_the_plain_text_part(
    alert_type: AlertType, word: str
):
    """The plain-text part is what a person reads when their client shows no HTML. It
    used to be the Telegram message, which is written for a different reader.

    Addresses are taken out before the words are read. The rule is about *prose* — a
    reader should never meet "near_miss" in a sentence — and a link is not prose. Read
    whole, this failed on `/dashboard/lifecycles?tab=compliance_changes`, which is the
    real address of a real page and the correct thing to send somebody. The only way to
    pass would have been to break the link.
    """

    prose = _URL_IN_TEXT.sub(" ", _rendered(alert_type).text_body)
    assert word.lower() not in prose.lower(), word


def test_a_screening_status_is_quoted_and_never_concluded():
    rendered = _rendered(AlertType.CONFIRMED)

    assert "eligible" in rendered.text_body
    # It is stated as what was recorded, with when and under which methodology beside
    # it — never as a conclusion this message reached.
    assert "Screening status at evaluation" in rendered.text_body
    assert "AAOIFI v2.1" in rendered.text_body


def test_a_screening_change_says_what_actually_changed():
    """It used to say a status had changed and then show nothing about it, which is the
    shape of an alert nobody can act on."""

    rendered = _rendered(AlertType.COMPLIANCE)

    assert "under review" in rendered.text_body
    assert "2026-08-16" in rendered.text_body
    assert "2.1" in rendered.text_body


def test_a_missing_reading_is_never_filled_in_with_a_zero():
    """"We saw 0" about a value nobody managed to read is a false statement somebody
    could act on."""

    rendered = _rendered(AlertType.CONFIRMED)

    assert "We could not read it" in rendered.text_body
    assert "We saw 0" not in rendered.text_body


def test_a_monitor_with_no_trade_context_says_so_rather_than_inventing_one():
    rendered = _rendered(AlertType.CONFIRMED)
    assert "Research only" in rendered.text_body
    assert "You set no entry, stop or target" in rendered.text_body


def test_the_two_parts_of_the_email_agree():
    """A text part that says something different from the HTML part is one email making
    two claims. Every fact in the table has to be in the plain text as well."""

    rendered = _rendered(AlertType.CONFIRMED)
    for value in ("Steady majors", "binance 4h", "100%", "RSI below 35", "eligible"):
        assert value in rendered.text_body, value
        assert value in rendered.html_body, value


def test_every_email_says_why_it_arrived_and_where_to_change_it():
    for alert_type in _ALERTS:
        rendered = _rendered(alert_type)
        assert "You are receiving this because" in rendered.html_body
        assert "Connections" in rendered.html_body


def test_every_email_carries_the_product_boundary():
    for alert_type in _ALERTS:
        rendered = _rendered(alert_type)
        assert "does not execute trades" in rendered.html_body


# ── The page and the emails agree about what exists ──────────────────────────


def test_every_alert_type_has_plain_words_of_its_own():
    """The Connections page lists these as what will arrive. A type with no words falls
    back to "A message from Hilal Markets", which promises nothing."""

    named = {kind.label for kind in every_message_kind()}
    for alert_type in AlertType:
        label = message_kind(alert_type.value).label
        assert label in named, f"{alert_type.value} has no plain-words name"
