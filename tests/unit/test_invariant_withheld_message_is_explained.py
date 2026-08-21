"""A message the product decides not to send must leave a reason behind.

A monitor produced fifty alerts in two minutes and then went silent. Every one of the
fifty was written to the database, every one was sent nowhere, and not one of them
recorded why. The owner raised the limit on the Settings page, nothing changed, and the
one screen that could have explained it said "no notification destination was attempted"
and told them to switch a channel on — advice that was not merely vague but wrong, since
three channels were already on.

Two rules follow, and both are asserted for **every** reason the gate can give rather
than for the hourly limit that was reported:

* whatever the reason, it is written on the alert;
* whatever the reason, plain words exist for it that name the setting responsible.

A fix that only recorded the hourly limit would fail the loops below.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.services.alert_limits import (
    UNCHOSEN_MONITOR_HOURLY_LIMIT,
    effective_alerts_per_hour,
)
from ai_market_monitor.services.notification_preferences import DeliveryDecision
from ai_market_monitor.services.notifications import NotificationDispatcher
from ai_market_monitor.services.product_language import UNKNOWN_WHY, why_not_sent

#: Every code the delivery gate can return, and every code the scanner's own fatigue
#: guard can return. Listed here so a new reason added to either without words for it
#: fails this test rather than reaching a person as "We cannot say why".
EVERY_REASON = [
    "hourly_message_limit_reached",
    "daily_message_limit_reached",
    "outside_chosen_hours",
    "coin_silenced",
    "coin_silenced_on_this_monitor",
    "monitor_silenced",
    "opportunity_silenced",
    "near_miss_messages_off",
    "below_near_miss_threshold",
    "progress_messages_off",
    "compliance_messages_off",
    "no_way_of_being_told_is_on",
    "chosen_channel_not_connected",
    "duplicate_event_hash",
    "symbol_cooldown",
    "maximum_alerts_per_hour",
    "daily_alert_budget",
    "weekly_alert_budget",
    "trial_alert_limit_reached",
]


class _Alert:
    """Only the two fields the recorder touches."""

    def __init__(self, suppressed_reason: str | None = None) -> None:
        self.suppressed_reason = suppressed_reason


@pytest.mark.parametrize("reason", EVERY_REASON)
def test_a_message_sent_nowhere_records_why(reason: str) -> None:
    alert = _Alert()
    NotificationDispatcher._record_silence(alert, [], reason)
    assert alert.suppressed_reason == reason


@pytest.mark.parametrize("reason", EVERY_REASON)
def test_every_reason_has_words_that_name_the_setting(reason: str) -> None:
    """"We cannot say why" is not an answer, and it is what an unknown code produces."""

    words = why_not_sent(reason)
    assert words is not UNKNOWN_WHY, f"{reason} reaches a person with no explanation"
    assert words.headline and words.meaning and words.what_to_do
    # The *meaning* has to be about this reason, never the fallback's shrug. What to do
    # is allowed to be "Nothing to do." — for a duplicate or a spent trial that is the
    # honest answer, and inventing a step there would send somebody to change a setting
    # that had nothing to do with it.
    assert words.meaning != UNKNOWN_WHY.meaning
    assert words.headline != UNKNOWN_WHY.headline


def test_a_delivered_message_records_nothing() -> None:
    """A reason on an alert that was delivered would be a false record."""

    alert = _Alert()
    NotificationDispatcher._record_silence(alert, [object()], "hourly_message_limit_reached")
    assert alert.suppressed_reason is None


def test_silence_with_nothing_refusing_it_is_still_explained() -> None:
    """Nothing refused the message and nothing carried it — the hole this exists to close.

    The gate allows Telegram, the account has no live Telegram link, the queue stays
    empty and no reason is given. That is the same silence as the reported one, reached
    from the other side.
    """

    alert = _Alert()
    NotificationDispatcher._record_silence(alert, [], None)
    assert alert.suppressed_reason == "chosen_channel_not_connected"
    assert why_not_sent(alert.suppressed_reason) is not UNKNOWN_WHY


def test_a_reason_already_recorded_is_never_overwritten() -> None:
    """The scanner records its own suppressions first, and its answer is the earlier one."""

    alert = _Alert("trial_alert_limit_reached")
    NotificationDispatcher._record_silence(alert, [], "hourly_message_limit_reached")
    assert alert.suppressed_reason == "trial_alert_limit_reached"


def test_a_reason_is_truncated_rather_than_raising() -> None:
    """A diagnostic must never become the failure — the column holds 160 characters."""

    alert = _Alert()
    NotificationDispatcher._record_silence(alert, [], "x" * 500)
    assert alert.suppressed_reason is not None
    assert len(alert.suppressed_reason) == 160


def test_a_decision_that_allows_a_channel_carries_no_reason() -> None:
    decision = DeliveryDecision({"web"})  # type: ignore[arg-type]
    assert decision.blocked_by is None
    assert not decision.delivered_nowhere


class TestTheSettingsNumberGoverns:
    """The number on the Settings page has to change what actually arrives."""

    def test_a_monitor_limit_nobody_chose_defers_to_the_account_limit(self) -> None:
        """Fifty is the schema's default, not a decision, and it capped a chosen 500."""

        assert (
            effective_alerts_per_hour(
                monitor_limit=UNCHOSEN_MONITOR_HOURLY_LIMIT, account_limit=500
            )
            == 500
        )

    @pytest.mark.parametrize("account_limit", [1, 5, 36, 120, 500, 1000])
    def test_raising_the_settings_number_raises_what_arrives(self, account_limit: int) -> None:
        assert (
            effective_alerts_per_hour(
                monitor_limit=UNCHOSEN_MONITOR_HOURLY_LIMIT, account_limit=account_limit
            )
            == account_limit
        )

    def test_a_deliberately_quiet_monitor_stays_quiet(self) -> None:
        """A chosen per-monitor limit is a decision and a bigger account budget cannot undo it."""

        assert effective_alerts_per_hour(monitor_limit=5, account_limit=500) == 5

    def test_the_account_limit_still_caps_a_chatty_monitor(self) -> None:
        assert effective_alerts_per_hour(monitor_limit=900, account_limit=10) == 10

    @pytest.mark.parametrize("monitor_limit", [1, 2, 5, 49, 51, 900])
    @pytest.mark.parametrize("account_limit", [1, 10, 500])
    def test_the_result_is_never_zero_or_negative(
        self, monitor_limit: int, account_limit: int
    ) -> None:
        """A limit of zero would silence every monitor for ever, and fail closed the wrong way."""

        assert effective_alerts_per_hour(
            monitor_limit=monitor_limit, account_limit=account_limit
        ) >= 1
