"""One sample of every email Hilal Markets can send, previewed or delivered for real.

Why one script rather than one per template: the product sends fifteen different emails
from six different renderers, and the only way to see that they look like one product is
to put them side by side. A script per template is how they drift.

Nothing here writes email copy. Every sample calls the renderer the product itself
calls, so what arrives is what a customer would get. Only the *data* is sample data, and
it is deliberately obvious about it — the coin is ``SMPL`` and the person is a test
account, because a screening status is a governed record and a sample must never look
like one about a real asset.

Safe by default. Without ``--live`` it writes previews to disk and sends nothing.

    .venv/Scripts/python scripts/send_email_samples.py --list
    .venv/Scripts/python scripts/send_email_samples.py
    .venv/Scripts/python scripts/send_email_samples.py \
        --live --confirm LIVE_EMAIL_SAMPLE_TEST --recipient you@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from pydantic import AnyHttpUrl

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.services.alert_emails import AlertEmailRenderer
from ai_market_monitor.services.alert_presentation import (
    AlertActionPresentation,
    AlertConditionPresentation,
    AlertPresentation,
)
from ai_market_monitor.services.email_branding import HilalMarketsEmailRenderer
from ai_market_monitor.services.email_delivery import AuthEmailService
from ai_market_monitor.services.payment_emails import PaymentEmailRenderer

LIVE_CONFIRMATION = "LIVE_EMAIL_SAMPLE_TEST"

#: Seconds between two live sends. The provider rate-limits, and a burst that trips the
#: limit fails the samples at the end of the list rather than the ones being looked at.
SEND_GAP_SECONDS = 1.2

#: A coin that does not exist, used everywhere a sample needs one.
#:
#: Not BTC and not ETH, on purpose. These emails quote a *screening status*, which is a
#: governed record with evidence behind it. A sample that showed a status next to a real
#: ticker would be a screening claim nobody reviewed, sitting in an inbox looking exactly
#: like the real thing.
SAMPLE_SYMBOL = "SMPL-USDT"
SAMPLE_ASSET = "Sample Coin"

#: Where the links in a sample should point.
#:
#: A developer machine sets ``PUBLIC_BASE_URL=http://localhost:8000``, and a sample built
#: from that is misleading in two ways at once: every button goes somewhere only that
#: machine can open, and the Evidence Passport row silently stops being a link at all,
#: because the renderer only turns a value into one when the address is ``https``. So a
#: sample looked broken when the code was working exactly as written.
DEFAULT_SAMPLE_BASE_URL = "https://hilalmarkets.com"


@dataclass(frozen=True, slots=True)
class Sample:
    """One email, ready to look at or to send."""

    key: str
    what_it_is: str
    when_it_is_sent: str
    subject: str
    text_body: str
    html_body: str
    purpose: str


# ── The sample data every alert is built from ────────────────────────────────


def _presentation(
    settings: Settings,
    *,
    alert_type: str,
    title: str,
    body: str,
    passed: list[AlertConditionPresentation],
    missing: list[AlertConditionPresentation],
    lifecycle_state: str,
    setup_score: float | None,
    with_trade_context: bool = True,
) -> AlertPresentation:
    base = str(settings.public_base_url).rstrip("/")
    return AlertPresentation(
        alert_id="00000000-0000-0000-0000-0000000005a1",
        strategy_id="00000000-0000-0000-0000-0000000005b2",
        strategy_version="3",
        alert_type=alert_type,
        title=title,
        body=body,
        symbol=SAMPLE_SYMBOL,
        direction="long",
        strategy="Steady movers",
        exchange="Binance",
        timeframe="4h",
        setup_score=setup_score,
        passed_conditions=passed,
        missing_conditions=missing,
        entry_zone="1.180 – 1.215" if with_trade_context else "n/a",
        stop="1.128" if with_trade_context else "n/a",
        stop_distance="4.4% below entry" if with_trade_context else "n/a",
        targets=["1.310", "1.395"] if with_trade_context else [],
        reward_to_risk="1 : 2.4" if with_trade_context else "n/a",
        data_freshness="18 Aug 2026, 09:40 UTC",
        trust_score=92.0,
        trust_grade="High",
        trust_summary="Every price used was a closed candle from the exchange.",
        setup_age="2 hours",
        lifecycle_state=lifecycle_state,
        chart_reference=None,
        proof_url=f"{base}/dashboard/alerts/sample/proof",
        dashboard_url=f"{base}/dashboard",
        sharia_status="eligible",
        sharia_methodology="Sample methodology (illustration only)",
        sharia_reviewed_at="12 Aug 2026",
        sharia_passport_url=f"{base}/dashboard/market/smpl",
        actions=[
            AlertActionPresentation(
                "See what happened", "opportunity", f"{base}/dashboard/opportunities"
            ),
            AlertActionPresentation(
                "See the evidence", "evidence", f"{base}/dashboard/market/smpl"
            ),
        ],
    )


def _condition(name: str, *, passed: bool, actual: str, required: str):
    return AlertConditionPresentation(
        name=name,
        state="passed" if passed else "missing",
        actual_value=actual,
        required_value=required,
    )


def _alert_samples(settings: Settings) -> list[Sample]:
    renderer = AlertEmailRenderer(settings)

    price_met = _condition(
        "Price above the 50-day average", passed=True, actual="1.204", required="above 1.150"
    )
    volume_met = _condition(
        "Trading volume above normal", passed=True, actual="1.9x normal", required="above 1.5x"
    )
    rsi_missing = _condition(
        "RSI back under 70", passed=False, actual="74", required="at most 70"
    )

    definitions = [
        (
            "alert-match",
            "Alert — everything you asked for happened",
            "Every condition on one of your Watchlists became true for a coin.",
            _presentation(
                settings,
                alert_type="confirmed",
                title="Everything you asked for happened",
                body="",
                passed=[price_met, volume_met, _condition(
                    "RSI back under 70", passed=True, actual="66", required="at most 70"
                )],
                missing=[],
                lifecycle_state="confirmed",
                setup_score=100.0,
            ),
        ),
        (
            "alert-near-miss",
            "Alert — nearly there",
            "A coin came close to everything on your list, but one part was still missing.",
            _presentation(
                settings,
                alert_type="near_miss",
                title="Nearly there",
                body="",
                passed=[price_met, volume_met],
                missing=[rsi_missing],
                lifecycle_state="near_confirmation",
                setup_score=82.0,
            ),
        ),
        (
            "alert-forming",
            "Alert — something started forming",
            "Part of what you asked for became true for a coin.",
            _presentation(
                settings,
                alert_type="forming",
                title="Something started forming",
                body="",
                passed=[price_met],
                missing=[
                    _condition(
                        "Trading volume above normal",
                        passed=False,
                        actual="1.1x normal",
                        required="above 1.5x",
                    ),
                    rsi_missing,
                ],
                lifecycle_state="forming",
                setup_score=41.0,
            ),
        ),
        (
            "alert-lifecycle",
            "Alert — something changed",
            "An opportunity you were already watching moved to a different stage.",
            _presentation(
                settings,
                alert_type="lifecycle",
                title="Something changed",
                body="",
                passed=[price_met, volume_met],
                missing=[rsi_missing],
                lifecycle_state="expired",
                setup_score=64.0,
            ),
        ),
        (
            "alert-failure",
            "Alert — a check could not run",
            "The market numbers one of your Watchlists needed could not be read.",
            _presentation(
                settings,
                alert_type="failure",
                title="A check could not run",
                body="",
                passed=[],
                missing=[
                    AlertConditionPresentation(
                        name="Price above the 50-day average",
                        state="missing",
                        actual_value=None,
                        required_value="above 1.150",
                    )
                ],
                lifecycle_state="error",
                setup_score=None,
                with_trade_context=False,
            ),
        ),
    ]

    samples = [
        Sample(
            key=key,
            what_it_is=what,
            when_it_is_sent=when,
            subject=(rendered := renderer.render(presentation)).subject,
            text_body=rendered.text_body,
            html_body=rendered.html_body,
            purpose=f"sample_{key.replace('-', '_')}",
        )
        for key, what, when, presentation in definitions
    ]

    # The two account notices take the other shape: a written title and body rather than
    # a set of readings. Both go through the same renderer.
    account_notice = _presentation(
        settings,
        alert_type="trial",
        title="Your free trial ends in three days",
        body=(
            "Your Hilal Markets trial ends on 21 August 2026. Nothing stops working "
            "before then, and nothing is charged automatically. If you would like to "
            "keep your Watchlists running after that date, you can choose a plan in "
            "your account."
        ),
        passed=[],
        missing=[],
        lifecycle_state="ending_soon",
        setup_score=None,
        with_trade_context=False,
    )
    trial = renderer.render(account_notice)
    samples.append(
        Sample(
            key="alert-account",
            what_it_is="Alert — about your account",
            when_it_is_sent="Something changed about the plan or the access.",
            subject=trial.subject,
            text_body=trial.text_body,
            html_body=trial.html_body,
            purpose="sample_alert_account",
        )
    )

    compliance_notice = _presentation(
        settings,
        alert_type="compliance",
        title="A coin on your list was reviewed again",
        body=(
            f"{SAMPLE_ASSET} was reviewed again and its screening status moved. This is "
            "a sample message showing how a screening change is reported. The status, "
            "the methodology and the review date always come from the recorded review "
            "and its evidence — never from a price, and never from this message."
        ),
        passed=[],
        missing=[],
        lifecycle_state="under review",
        setup_score=None,
        with_trade_context=False,
    )
    compliance = renderer.render(compliance_notice)
    samples.append(
        Sample(
            key="alert-screening",
            what_it_is="Alert — Shariah status changed",
            when_it_is_sent="A coin was reviewed again and its recorded status moved.",
            subject=compliance.subject,
            text_body=compliance.text_body,
            html_body=compliance.html_body,
            purpose="sample_alert_screening",
        )
    )
    return samples


# ── Everything else the product sends ────────────────────────────────────────


def _branded_samples(settings: Settings) -> list[Sample]:
    frame = HilalMarketsEmailRenderer(settings)
    codes = [
        ("login", "123 456", "One-time code — signing in"),
        ("signup", "204 881", "One-time code — confirming a new email"),
        ("password_reset", "710 355", "One-time code — resetting a password"),
        ("system_brain", "948 026", "One-time code — administrator sign-in"),
    ]
    samples: list[Sample] = []
    for purpose, code, what in codes:
        rendered = frame.auth_code(
            recipient="sample@hilalmarkets.com",
            code=code,
            purpose=purpose,
            ttl_minutes=(
                settings.system_brain_otp_ttl_minutes
                if purpose == "system_brain"
                else settings.auth_code_ttl_minutes
            ),
        )
        samples.append(
            Sample(
                key=f"code-{purpose.replace('_', '-')}",
                what_it_is=what,
                when_it_is_sent="Somebody asked for a code to continue.",
                subject=rendered.subject,
                text_body=rendered.text_body,
                html_body=rendered.html_body,
                purpose=f"sample_code_{purpose}",
            )
        )

    welcome = frame.signup_welcome(first_name="Amr", email="sample@hilalmarkets.com")
    samples.append(
        Sample(
            key="signup-welcome",
            what_it_is="Sign up — the account is ready",
            when_it_is_sent="Sent once, straight after the account is created.",
            subject=welcome.subject,
            text_body=welcome.text_body,
            html_body=welcome.html_body,
            purpose="sample_signup_welcome",
        )
    )

    access = frame.access_changed(
        first_name="Amr",
        plan_name="Pro",
        duration_label="12 months",
        ends_at_label="18 Aug 2027, 00:00 UTC",
    )
    samples.append(
        Sample(
            key="access-changed",
            what_it_is="Account — the access changed",
            when_it_is_sent="Sent when the team changes what an account can reach.",
            subject=access.subject,
            text_body=access.text_body,
            html_body=access.html_body,
            purpose="sample_access_changed",
        )
    )

    test = frame.connection_test()
    samples.append(
        Sample(
            key="connection-test",
            what_it_is="Connections — “send me a test”",
            when_it_is_sent="Sent when somebody presses the test button on Connections.",
            subject=test.subject,
            text_body=test.text_body,
            html_body=test.html_body,
            purpose="sample_connection_test",
        )
    )
    return samples


def _payment_sample(settings: Settings) -> Sample:
    now = datetime.now(UTC)
    rendered = PaymentEmailRenderer(settings).render(
        first_name="Amr",
        plan_name="Pro",
        billing_frequency="monthly",
        amount=Decimal("29.00"),
        currency="USD",
        payment_date=now,
        period_end_date=now + timedelta(days=30),
        renews_automatically=True,
        receipt_url=None,
        plan_limits={
            "active_strategies": 10,
            "symbols_per_strategy": 100_000,
            "on_demand_scans_per_month": 60,
        },
    )
    return Sample(
        key="payment-success",
        what_it_is="Billing — the payment went through",
        when_it_is_sent="Sent after a payment is confirmed by the provider.",
        subject=rendered.subject,
        text_body=rendered.text_body,
        html_body=rendered.html_body,
        purpose="sample_payment_success",
    )


def _support_samples() -> list[Sample]:
    """The automated support reply, and the copies the office receives.

    The wording is lifted from the services that send them rather than written again
    here. A sample that says something the product does not say is worse than no sample.
    """

    from ai_market_monitor.services.public_chat import PublicChatService
    from ai_market_monitor.services.public_forms import PublicFormsService

    reference = "HM-20260818-5A3C9F21"

    class _Inquiry:
        """Only the fields the renderer reads. No database is touched by a sample."""

        name = "Amr"
        details = (
            "Assalamu Alaikum. I would like to understand how a coin gets its screening "
            "status, and what evidence is kept behind it."
        )
        category = "product_question"
        normalized_email = "sample@hilalmarkets.com"
        submitted_at = datetime.now(UTC)
        source_page = "/help"
        referrer = None
        attribution: dict[str, str] = {}
        knowledge_gap_category = "screening_process"
        support_metadata: dict[str, str] = {}

    inquiry = _Inquiry()
    inquiry.reference = reference  # type: ignore[attr-defined]

    samples: list[Sample] = []
    for recipient_kind, key, what, when in (
        (
            "customer",
            "support-automatic-reply",
            "Automated support — the reply the person gets",
            "Sent the moment somebody asks for help, before a person has answered.",
        ),
        (
            "office",
            "support-office-copy",
            "Automated support — the copy the office gets",
            "Sent to the office at the same time, with everything needed to answer.",
        ),
    ):
        subject, text, html_body = PublicChatService._render_email(
            PublicChatService.__new__(PublicChatService),  # type: ignore[arg-type]
            inquiry,  # type: ignore[arg-type]
            recipient_kind,
        )
        samples.append(
            Sample(
                key=key,
                what_it_is=what,
                when_it_is_sent=when,
                subject=subject,
                text_body=text,
                html_body=html_body,
                purpose=f"sample_{key.replace('-', '_')}",
            )
        )

    class _ContactSubmission:
        title = "A question about the Evidence Passport"
        description = (
            "Where can I read the evidence behind a coin's status, and who reviewed it?"
        )
        normalized_email = "sample@hilalmarkets.com"
        submitted_at = datetime.now(UTC)
        source_page = "/contact"

    subject, text, html_body = PublicFormsService._contact_email(
        _ContactSubmission()  # type: ignore[arg-type]
    )
    samples.append(
        Sample(
            key="contact-form",
            what_it_is="Contact form — the copy the office gets",
            when_it_is_sent="Sent when somebody fills in the contact page.",
            subject=subject,
            text_body=text,
            html_body=html_body,
            purpose="sample_contact_form",
        )
    )
    return samples


def _operations_sample(settings: Settings) -> Sample:
    """The page an operator gets when something in the platform stops working.

    Deliberately plain text inside a <pre>. It is read on a phone in the middle of the
    night and there is nothing about it to style.
    """

    from html import escape

    subject = "Hilal Markets alert: market data adapter is failing"
    body = (
        "SEVERITY: high\n"
        "COMPONENT: market data adapter\n"
        "SINCE: 18 Aug 2026, 09:12 UTC\n"
        "FAILING CHECKS: 3 of 12\n\n"
        "The exchange adapter refused three price requests in a row. Watchlist checks "
        "that need those prices are being retried and will report a failure to the "
        "people who own them if the next attempt does not succeed.\n\n"
        "This is a sample. Nothing is actually failing.\n"
    )
    return Sample(
        key="operations-alert",
        what_it_is="Operations — something in the platform needs attention",
        when_it_is_sent="Sent to the operator on call, never to a customer.",
        subject=subject,
        text_body=body,
        html_body=(
            HilalMarketsEmailRenderer(settings).ensure_shell(
                subject=subject,
                preheader="A component of the platform needs attention.",
                html_body=f"<pre>{escape(body)}</pre>",
            )
        ),
        purpose="sample_operations_alert",
    )


def _support_ticket_sample(settings: Settings) -> Sample:
    """The ticket the office receives when somebody reports a problem from the app."""

    import json

    from ai_market_monitor.services.email_delivery import _escape_email_text

    ticket_id = UUID("00000000-0000-0000-0000-000000007c41")
    user_id = UUID("00000000-0000-0000-0000-00000000c0de")
    subject_line = "The chart on my Watchlist will not load"
    email_subject = f"Hilal Markets support ticket: {subject_line}"
    context = {"page": "/dashboard/watchlists", "browser": "Chrome 148", "plan": "Pro"}
    body = (
        "A new Hilal Markets support ticket was created.\n\n"
        f"Ticket ID: {ticket_id}\n"
        f"User ID: {user_id}\n"
        "Requester email: sample@hilalmarkets.com\n"
        f"Subject: {subject_line}\n"
        "Screenshots: 0\n\n"
        "Description:\n"
        "The chart area stays empty when I open my Watchlist. Everything else loads.\n\n"
        "Context:\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2, default=str)}\n"
    )
    return Sample(
        key="support-ticket",
        what_it_is="Support ticket — the copy the office gets",
        when_it_is_sent="Sent when somebody reports a problem from inside the app.",
        subject=email_subject,
        text_body=body,
        html_body=HilalMarketsEmailRenderer(settings).ensure_shell(
            subject=email_subject,
            preheader=f"Support ticket {ticket_id} was created.",
            html_body=(
                '<div style="color:#50555e;font-size:14px;line-height:1.7;'
                'white-space:pre-wrap">'
                f"{_escape_email_text(body)}</div>"
            ),
        ),
        purpose="sample_support_ticket",
    )


def every_sample(settings: Settings) -> list[Sample]:
    """Every email the product can send, in the order a person meets them.

    The frame is put on here, once, for all of them. Several renderers hand back a bare
    body and rely on ``send_transactional`` to wrap it on the way out — which meant the
    preview written to disk was not the email that would arrive, and the support reply
    looked unbranded in review when the real one is not. Applying it here makes the
    preview and the delivery the same string by construction; the wrap on the way out
    then finds the marker and does nothing.
    """

    frame = HilalMarketsEmailRenderer(settings)
    return [
        Sample(
            key=sample.key,
            what_it_is=sample.what_it_is,
            when_it_is_sent=sample.when_it_is_sent,
            subject=sample.subject,
            text_body=sample.text_body,
            html_body=frame.ensure_shell(
                subject=sample.subject,
                html_body=sample.html_body,
            ),
            purpose=sample.purpose,
        )
        for sample in (
            *_branded_samples(settings),
            *_alert_samples(settings),
            _payment_sample(settings),
            *_support_samples(),
            _support_ticket_sample(settings),
            _operations_sample(settings),
        )
    ]


# ── Running it ───────────────────────────────────────────────────────────────


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Name every sample and stop")
    parser.add_argument("--only", default="", help="Comma-separated sample keys")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_SAMPLE_BASE_URL,
        help="Where the links in the samples point (default: %(default)s)",
    )
    parser.add_argument("--recipient", help="Test recipient; required with --live")
    parser.add_argument("--live", action="store_true", help="Send through configured SMTP")
    parser.add_argument("--confirm", help=f"Required with --live: {LIVE_CONFIRMATION}")
    parser.add_argument(
        "--output-directory",
        default="reports/email-samples",
        help="Where the safe previews are written",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    # The delivery settings stay exactly as configured; only where the links point is
    # overridden, so a sample never carries a `localhost` button into somebody's inbox.
    settings = get_settings().model_copy(
        update={"public_base_url": AnyHttpUrl(args.base_url)}
    )
    samples = every_sample(settings)
    if args.only:
        wanted = {key.strip() for key in args.only.split(",") if key.strip()}
        unknown = wanted - {sample.key for sample in samples}
        if unknown:
            print(f"No sample named: {', '.join(sorted(unknown))}")
            return 2
        samples = [sample for sample in samples if sample.key in wanted]

    if args.list:
        width = max(len(sample.key) for sample in samples)
        for sample in samples:
            print(f"{sample.key.ljust(width)}  {sample.what_it_is}")
        return 0

    output = Path(args.output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    for sample in samples:
        (output / f"{sample.key}.html").write_text(sample.html_body, encoding="utf-8")
        (output / f"{sample.key}.txt").write_text(sample.text_body, encoding="utf-8")
    print(f"{len(samples)} sample(s) written to {output}")

    if not args.live:
        print("SAFE PREVIEW ONLY: nothing was sent.")
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

    service = AuthEmailService(settings)
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    print(f"\nLIVE: sending {len(samples)} sample(s) to {args.recipient}.\n")
    failures = 0
    for index, sample in enumerate(samples, 1):
        label = f"[{index}/{len(samples)}] {sample.key}"
        try:
            message_id = await service.send_transactional(
                recipient=args.recipient,
                subject=sample.subject,
                text_body=sample.text_body,
                html_body=sample.html_body,
                idempotency_key=f"sample-{sample.key}-{stamp}",
                purpose=sample.purpose,
            )
        except Exception as exc:  # noqa: BLE001 - every failure is reported, none hidden
            failures += 1
            print(f"{label}: FAILED — {exc.__class__.__name__}: {exc}")
        else:
            print(f"{label}: sent — {sample.subject}  ({message_id})")
        if index < len(samples):
            time.sleep(SEND_GAP_SECONDS)

    print(f"\n{len(samples) - failures} sent, {failures} failed.")
    return 1 if failures else 0


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
