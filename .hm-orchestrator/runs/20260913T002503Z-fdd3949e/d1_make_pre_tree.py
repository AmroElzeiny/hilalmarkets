"""Behavioural revert of WP D1 in a COPY of the working tree, never in the tree itself.

The working tree already carried other workers' uncommitted changes to `billing.py` and
`plan_replacements.py` when this package started, so `git HEAD` is not a valid baseline.
This copies `src/`, `tests/` and the config files to a scratch directory and restores the
behaviour this package changed, using whole-region replacements with markers asserted
unique. The methods this package added and left uncalled are dead code and stay; what
matters is that no refund event reaches them.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

REPO = pathlib.Path(r"C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\Trading_assistant")
DEST = pathlib.Path(r"C:\Users\amroe\AppData\Local\Temp\opencode\d1_pre")
BILLING = "src/ai_market_monitor/services/billing.py"
PLAN = "src/ai_market_monitor/services/plan_replacements.py"

# The `_apply_event` refund handling exactly as it stood before this package.
ORIGINAL_APPLY_TAIL = '''        if event_type in {"payment.refunded", "refund.created"}:
            subscription = await self._upsert_subscription(
                provider=provider,
                data=data,
                forced_status=SubscriptionStatus.CANCELED,
            )
            subscription.canceled_at = datetime.now(UTC)
            await EntitlementService(self.session).snapshot(subscription.user_id)
            await EntitlementService(self.session).pause_excess_after_downgrade(
                subscription.user_id
            )
            self._audit(
                subscription.user_id,
                "billing.payment_refunded",
                "subscription",
                subscription.id,
                {},
            )
            return subscription
        if event_type in {"payment.expired", "payment.failed"}:
            user_id = self._parse_uuid(data.get("user_id"))
            if user_id:
                self._audit(user_id, f"billing.{event_type}", "user", user_id, {})
            return None
        if event_type in {"charge.refunded", "charge.dispute.created", "dispute.created"}:
            user_id = self._parse_uuid(data.get("user_id"))
            if user_id:
                self._audit(user_id, f"billing.{event_type}", "user", user_id, {})
        return None
'''

ORIGINAL_APPLY_START = (
    "        if event_type in REFUND_EVENT_TYPES:\n"
    "            # The money is going back,"
)
ORIGINAL_APPLY_STOP = "\n    async def _end_refunded_plan(\n"

HYDRATE_START = (
    "        if event_type in REFUND_EVENT_TYPES:\n"
    "            # Money coming back"
)
HYDRATE_STOP = (
    '        raw_attempt_id = data.get("checkout_attempt_id")\n'
    "        if raw_attempt_id in (None, \"\"):\n"
            "            requires_checkout"
)

RECORD_START = (
    "        if event_type in REFUND_EVENT_TYPES:\n"
    "            # The money came back."
)
RECORD_STOP = (
    "        elif event_type in {\n"
    '            "checkout.session.completed",\n'
    '            "invoice.payment_succeeded",\n'
    '            "payment.finished",\n'
    '            "subscription.paid",\n'
    '            "subscription.trialing",\n'
    '        } and normalized_status'
)


def once(text: str, needle: str, label: str) -> int:
    count = text.count(needle)
    assert count == 1, f"{label}: found {count} copies, expected 1"
    return text.index(needle)


def region(text: str, start: str, stop: str, label: str) -> tuple[int, int]:
    i = once(text, start, label + ":start")
    j = once(text, stop, label + ":stop")
    assert i < j, f"{label}: markers out of order"
    return i, j


def main() -> int:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    for name in ("src", "tests"):
        shutil.copytree(
            REPO / name, DEST / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    for name in ("pyproject.toml", "alembic.ini"):
        source = REPO / name
        if source.exists():
            shutil.copy2(source, DEST / name)

    billing_path = DEST / BILLING
    text = billing_path.read_text(encoding="utf-8")

    # 1. `_apply_event`: restore the two original refund branches and the audit tail.
    start, stop = region(text, ORIGINAL_APPLY_START, ORIGINAL_APPLY_STOP, "_apply_event")
    text = text[:start] + ORIGINAL_APPLY_TAIL + text[stop + 1 :]

    # 2. `_hydrate_checkout_data`: remove the refund early return.
    start, stop = region(text, HYDRATE_START, HYDRATE_STOP, "_hydrate_checkout_data")
    text = text[:start] + text[stop:]

    # 3. The one-time crypto guard back to `completed` alone, comment with it.
    condition = "            and attempt.status in {SETTLED_ATTEMPT_STATUS, REFUNDED_ATTEMPT_STATUS}\n"
    assert text.count(condition) == 1
    text = text.replace(condition, '            and attempt.status == "completed"\n', 1)
    comment = """            # ``refunded`` is in this check because the money behind a one-time crypto
            # invoice came back after it was paid. Without it, a re-delivered
            # ``payment.finished`` — NOWPayments sends the invoice status again — would
            # pass as never-settled, because the refund has just moved the row off
            # ``completed``, and would hand out a second 30-day period for money this
            # product no longer holds.
"""
    assert text.count(comment) == 1
    text = text.replace(comment, "", 1)

    # 3b. The tolerant id reader this package added goes back, with its two call sites.
    helper_start = once(text, "    @classmethod\n    def _uuid_if_readable(", "tolerant reader")
    helper_end = once(text, "    @staticmethod\n    def _parse_datetime(", "next staticmethod")
    text = text[:helper_start] + text[helper_end:]
    text = text.replace("self._uuid_if_readable(", "self._parse_uuid(")
    text = text.replace("BillingService._uuid_if_readable(", "BillingService._parse_uuid(")
    text = text.replace(
        '''        # Read without raising: an unreadable person id must not cost the product the
        # stored event. A payment whose checkout cannot be matched has already been
        # refused by ``_hydrate_checkout_data`` above, which runs first.
        user_id = self._parse_uuid(data.get("user_id"))''',
        '        user_id = self._parse_uuid(data.get("user_id"))',
        1,
    )

    # 4. `_record_checkout_event`: drop the refund reading, restore `completed` and the
    #    old failure set that carried `payment.refunded`.
    start, stop = region(text, RECORD_START, RECORD_STOP, "_record_checkout_event")
    text = text[:start] + "        if event_type in {\n" + text[stop + len("        elif event_type in {\n") :]
    text = text.replace(
        '            attempt.status = SETTLED_ATTEMPT_STATUS\n',
        '            attempt.status = "completed"\n',
        1,
    )
    text = text.replace(
        '            "payment.partially_paid",\n        }:',
        '            "payment.partially_paid",\n            "payment.refunded",\n        }:',
        1,
    )
    text = text.replace(
        """        if attempt_id is None:
            # A refund with nothing here was already reported by
            # ``_attach_refund_to_attempt``, which is where the searching happens. Every
            # event reaches this function through that one, so alerting again would count
            # one refund twice.
            return""",
        "        if attempt_id is None:\n            return",
        1,
    )
    billing_path.write_text(text, encoding="utf-8")

    plan_path = DEST / PLAN
    plan = plan_path.read_text(encoding="utf-8")
    amount_start = once(plan, "        amount = (\n", "money amount")
    amount_stop = once(plan, "        if amount <= 0:\n", "amount guard")
    plan = (
        plan[:amount_start]
        + """        amount = money_owed_for_unused_time(
            paid_amount=source.amount,
            period_start=period_start,
            period_end=period_end,
            ended_at=moment,
        )
"""
        + plan[amount_stop:]
    )
    plan = plan.replace(
        "            BillingCheckoutAttempt.status == SETTLED_PAYMENT_STATUS,",
        '            BillingCheckoutAttempt.status == "completed",',
        1,
    )
    plan_path.write_text(plan, encoding="utf-8")

    # Report what the reverted tree now looks like, so the run is auditable.
    billing_after = billing_path.read_text(encoding="utf-8")
    print("reverted tree:")
    print("  refund event routed in _apply_event:", ORIGINAL_APPLY_START in billing_after)
    print("  hydrate refund early return:", "Money coming back is read by its own rules" in billing_after)
    print("  payment.refunded in failure set:", '"payment.partially_paid",\n            "payment.refunded",' in billing_after)
    plan_after = plan_path.read_text(encoding="utf-8")
    print("  money owed reads the frozen row unconditionally:", "        amount = money_owed_for_unused_time(" in plan_after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
