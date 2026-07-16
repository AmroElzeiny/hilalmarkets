"""Preview a payment email safely; live SMTP requires explicit operator consent."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.services.email_delivery import AuthEmailService
from ai_market_monitor.services.payment_emails import PaymentEmailRenderer

LIVE_CONFIRMATION = "LIVE_PAYMENT_EMAIL_TEST"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipient", help="Test recipient; required with --live")
    parser.add_argument("--live", action="store_true", help="Send through configured SMTP")
    parser.add_argument("--confirm", help=f"Required with --live: {LIVE_CONFIRMATION}")
    parser.add_argument(
        "--output-directory",
        default="reports/payment-email-preview",
        help="Directory for the safe HTML/plain-text preview",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    rendered = PaymentEmailRenderer(settings).render(
        first_name="Test User",
        plan_name="Pro",
        billing_frequency="monthly",
        amount=Decimal("29.00"),
        currency="USD",
        payment_date=datetime.now(UTC),
        renewal_date=datetime.now(UTC) + timedelta(days=30),
        receipt_url=None,
        plan_limits={
            "active_strategies": 10,
            "symbols_per_strategy": 100_000,
            "on_demand_scans_per_month": 60,
        },
    )
    output = Path(args.output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    html_path = output / "payment_success.html"
    text_path = output / "payment_success.txt"
    html_path.write_text(rendered.html_body, encoding="utf-8")
    text_path.write_text(rendered.text_body, encoding="utf-8")
    print(f"Subject: {rendered.subject}")
    print(f"HTML preview: {html_path}")
    print(f"Plain-text preview: {text_path}")
    if not args.live:
        print("SAFE PREVIEW ONLY: no email was sent.")
        return 0
    if args.confirm != LIVE_CONFIRMATION:
        print(f"Live delivery blocked. Pass --confirm {LIVE_CONFIRMATION}.")
        return 2
    if not args.recipient:
        print("Live delivery blocked. Pass --recipient with a dedicated test inbox.")
        return 2
    if settings.email_adapter != "smtp":
        print("Live delivery blocked. EMAIL_ADAPTER is not smtp.")
        return 2
    print(f"LIVE TEST: sending the displayed payment email to {args.recipient}.")
    message_id = await AuthEmailService(settings).send_transactional(
        recipient=args.recipient,
        subject=rendered.subject,
        text_body=rendered.text_body,
        html_body=rendered.html_body,
        idempotency_key=f"manual-payment-test-{datetime.now(UTC).date().isoformat()}",
        purpose="payment_success_manual_test",
    )
    print(f"Delivery accepted. Message reference: {message_id}")
    return 0


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
