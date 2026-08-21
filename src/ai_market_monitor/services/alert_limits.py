"""How many messages a person may be sent in an hour, and who decides it.

Two numbers carried this name and neither knew about the other:

* ``DashboardPreference.notification_preferences["maximum_alerts_per_hour"]`` — the one
  on the Settings page, labelled "Maximum messages per hour", account-wide.
* ``StrategyDefinition.alerts.maximum_alerts_per_hour`` — frozen inside each approved
  monitor, per monitor, invisible after approval and unreachable from any screen.

The second one stopped a monitor after fifty messages in two minutes. Its owner raised
the number on the Settings page, nothing changed, and nothing anywhere explained why —
because the number they raised was the other one.

They are not the same question. "How many messages do I want in an hour, in total" is an
account question; "how chatty may this one monitor be" is a monitor question. What was
wrong is that the monitor's number could silently override the account's, including when
nobody had ever chosen it — fifty is the schema's own default, not a decision.

So: a monitor limit that was never chosen defers to the account limit, and a monitor
limit that *was* chosen still cannot exceed the account-wide budget. Raising the number
on Settings now raises what actually arrives, which is what the control promises.
"""

from __future__ import annotations

from ai_market_monitor.schemas.strategy import AlertPolicy

__all__ = [
    "CHOSEN_CHANNEL_NOT_CONNECTED",
    "DELIVERY_BLOCK_DAILY_LIMIT",
    "DELIVERY_BLOCK_HOURLY_LIMIT",
    "UNCHOSEN_MONITOR_HOURLY_LIMIT",
    "effective_alerts_per_hour",
]

#: The value a monitor carries when nobody picked one. Read from the schema rather than
#: written out again here, so the two cannot drift and a changed default needs one edit.
UNCHOSEN_MONITOR_HOURLY_LIMIT: int = int(
    AlertPolicy.model_fields["maximum_alerts_per_hour"].default
)

#: Recorded on an alert that was created and then sent nowhere because a limit was
#: reached. Stored codes, so every surface says the same thing about the same event.
DELIVERY_BLOCK_HOURLY_LIMIT = "hourly_message_limit_reached"
DELIVERY_BLOCK_DAILY_LIMIT = "daily_message_limit_reached"

#: Recorded when nothing refused the message and nothing could carry it either — a
#: chosen channel with no live connection behind it, or email on an account with no
#: address. Distinct from "no way of being told is on", because here one *is* on.
CHOSEN_CHANNEL_NOT_CONNECTED = "chosen_channel_not_connected"


def effective_alerts_per_hour(*, monitor_limit: int, account_limit: int) -> int:
    """The most messages this one monitor may produce in an hour.

    ``monitor_limit`` is the number frozen in the approved monitor; ``account_limit`` is
    the number on the Settings page. A monitor limit still sitting at the schema default
    was never a choice, so the account limit governs it outright — otherwise a default
    nobody picked quietly caps a number the owner did pick.
    """

    if monitor_limit == UNCHOSEN_MONITOR_HOURLY_LIMIT:
        return max(1, account_limit)
    return max(1, min(monitor_limit, account_limit))
