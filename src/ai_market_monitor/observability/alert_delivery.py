"""Actually send the page-worthy alerts, at most once, through a route that works.

The rules in :mod:`ai_market_monitor.observability.alerts` decide *what* is wrong and
*where* the message should go. Until now nothing carried it: the rules were evaluated,
the result was shown on the admin health page, and a person had to already be looking
at that page to learn that nobody was looking at anything. This module is the carrier.

Three properties are the whole point, and each one is enforced rather than hoped for.

**At most once per problem, per window.** Rules are re-evaluated every minute. A
one-hour outage would otherwise send sixty identical messages, and the reliable result
of that is a muted channel. Every send is claimed by an ``idempotency_key`` built from
the rule, the issue's dedupe key and the current repeat window, and the key is unique
in the database — so two workers racing on the same firing alert produce one message.

**Never through the thing that is broken.** The route pair comes from the rule, which
already refuses a primary or fallback that depends on the watched service. When the
primary refuses at send time the message moves to the fallback and the row records
that it had to, so "we only heard about this on the second path" is visible afterwards
instead of invisible.

**Tickets are not delivered.** A ticket-worthy rule is recorded in the operational
issue queue and nothing is sent. Waking somebody for a slow page is how a person
learns to ignore the next message, which may be the outage.

Nothing customer-owned is written or sent. The body is built from the rule's own fixed
sentences plus the measured number; there is no path from a prompt, a Watchlist, a
religious status or a secret into any column or any message here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Final
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models.operations import OperationalAlertDelivery
from ai_market_monitor.observability.alerts import (
    ALERT_RULES_VERSION,
    DELIVERY_ROUTE_DEPENDENCIES,
    AlertRule,
    FiredAlert,
    evaluate_alert_rules,
)
from ai_market_monitor.observability.durable_metrics import load_recorder
from ai_market_monitor.observability.issues import (
    OperationalIssueService,
    dedupe_key_for_alert,
)
from ai_market_monitor.observability.labels import assert_no_sensitive_content

__all__ = [
    "AlertDeliveryOutcome",
    "OperationalAlertDispatcher",
    "RouteUnavailable",
    "delivery_idempotency_key",
    "render_alert_message",
]

logger = logging.getLogger(__name__)

#: How far back the alert rules read the stored measurements. Long enough that a quiet
#: minute does not read as "no data", short enough that a problem fixed an hour ago
#: does not keep paging.
ALERT_EVALUATION_MINUTES: Final[int] = 60

#: Longest message body any route will carry. Truncated, never raised on: a length cap
#: that throws while the system is reporting a failure turns the report into a second
#: failure.
_MAX_BODY_LENGTH: Final[int] = 1200

_MAX_ERROR_LENGTH: Final[int] = 240


class RouteUnavailable(RuntimeError):
    """A route that is not configured in this deployment, or refused the message.

    ``retryable`` separates "try again in a minute" from "this route will never work
    here". An unconfigured chat id is not a transient failure and must move straight
    to the fallback rather than retrying for an hour.
    """

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AlertDeliveryOutcome:
    """What one dispatch pass did. Plain counts, for the task result and the tests."""

    evaluated: int
    paged: int
    ticketed: int
    already_claimed: int

    def as_dict(self) -> dict[str, int]:
        return {
            "evaluated": self.evaluated,
            "paged": self.paged,
            "ticketed": self.ticketed,
            "already_claimed": self.already_claimed,
        }


def delivery_idempotency_key(
    rule_name: str,
    dedupe_key: str,
    *,
    moment: datetime,
    repeat_minutes: int,
) -> str:
    """The claim for one page: this rule, this problem, this repeat window.

    The window is a floor of absolute time, not "minutes since the last send", so
    every process computes the same window without asking the others. Two workers
    that fire the same rule in the same minute build the same key and the unique
    constraint lets exactly one of them through.
    """

    if repeat_minutes <= 0:
        raise ValueError("The alert repeat window must be at least one minute.")
    window = int(moment.timestamp()) // (repeat_minutes * 60)
    return f"{rule_name}:{dedupe_key}:{window}"[:200]


def render_alert_message(rule: AlertRule, measured: float | None) -> tuple[str, str]:
    """Subject and body for one page, built only from the rule's own sentences.

    Nothing is interpolated except the rule's declared text and the measured number.
    There is deliberately no parameter for a caller to pass free text in.
    """

    subject = f"[{rule.severity.upper()}] {rule.name}"
    reading = "no reading" if measured is None else f"{measured:.4g}"
    body = "\n".join(
        (
            f"What broke: {rule.what_broke}",
            f"Who is affected: {rule.blast_radius}",
            f"First safe move: {rule.first_mitigation}",
            f"Measured: {reading}",
            f"Runbook: docs/OPERATIONS.md{rule.runbook_anchor}",
            f"Rules version: {ALERT_RULES_VERSION}",
        )
    )
    return subject, body[:_MAX_BODY_LENGTH]


class OperationalAlertDispatcher:
    """Turns firing rules into at-most-one message each, and then sends them."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- claiming ------------------------------------------------------------

    async def dispatch_due(self, *, now: datetime | None = None) -> dict[str, int]:
        """Evaluate the rules against stored measurements and claim what must be sent.

        Reads through :func:`load_recorder`, so the objectives are measured across the
        whole deployment rather than across whichever process happens to run this.
        """

        moment = (now or datetime.now(UTC)).astimezone(UTC)
        recorder = await load_recorder(
            self.session,
            policy=self.settings.metric_retention_policy,
            minutes=ALERT_EVALUATION_MINUTES,
            now=moment,
        )
        fired = evaluate_alert_rules(recorder)
        issues = OperationalIssueService(self.session)
        paged = 0
        ticketed = 0
        claimed_already = 0
        for alert in fired:
            issue = await issues.record_fired_alert(alert, now=moment)
            if not alert.rule.delivered:
                # A ticket stops here on purpose. It is in the queue, with its history
                # and its count; nobody is woken for it.
                ticketed += 1
                continue
            created = await self._claim(alert, issue_id=issue.id, moment=moment)
            if created:
                paged += 1
            else:
                claimed_already += 1
        await self.session.commit()
        return AlertDeliveryOutcome(
            evaluated=len(fired),
            paged=paged,
            ticketed=ticketed,
            already_claimed=claimed_already,
        ).as_dict()

    async def _claim(
        self,
        alert: FiredAlert,
        *,
        issue_id: UUID | None,
        moment: datetime,
    ) -> bool:
        rule = alert.rule
        key = delivery_idempotency_key(
            rule.name,
            dedupe_key_for_alert(alert),
            moment=moment,
            repeat_minutes=self.settings.operational_alert_repeat_minutes,
        )
        existing = await self.session.scalar(
            select(OperationalAlertDelivery).where(
                OperationalAlertDelivery.idempotency_key == key
            )
        )
        if existing is not None:
            return False
        subject, body = render_alert_message(rule, alert.measured)
        payload = {
            "subject": subject,
            "body": body,
            "runbook_anchor": rule.runbook_anchor,
            "watched_service": rule.watched_service,
            "rules_version": alert.rules_version,
        }
        # The same content check the metric labels and the issue rows go through, with
        # the length limit raised to the body's own declared cap. The body is several
        # sentences by design and all of them are ours; the credential and seed-phrase
        # tests still run over every character. This can only fire if somebody later
        # adds a caller-supplied field — which is exactly when it should.
        assert_no_sensitive_content(
            payload, field="alert_delivery.payload", max_length=_MAX_BODY_LENGTH
        )
        assert rule.primary_route is not None  # guaranteed by validate_alert_rules
        self.session.add(
            OperationalAlertDelivery(
                idempotency_key=key,
                rule_name=rule.name,
                severity=rule.severity,
                route=rule.primary_route,
                primary_route=rule.primary_route,
                fallback_route=rule.fallback_route,
                status="pending",
                payload=payload,
                issue_id=issue_id,
                next_retry_at=moment,
                created_at=moment,
            )
        )
        try:
            await self.session.flush()
        except IntegrityError:
            # Another process claimed the same window between the read and the write.
            # One message is the correct outcome, so this is a success, not an error.
            await self.session.rollback()
            return False
        return True

    # -- sending -------------------------------------------------------------

    async def process_due(
        self,
        *,
        limit: int = 25,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Send every claimed page that is due, moving to the fallback if needed."""

        moment = (now or datetime.now(UTC)).astimezone(UTC)
        rows = await self._due_rows(limit=limit, moment=moment)
        result = {"processed": 0, "sent": 0, "retryable": 0, "failed": 0, "fell_back": 0}
        for row in rows:
            row.status = "sending"
            row.attempt_count += 1
            row.last_attempt_at = moment
            row.next_retry_at = None
            await self.session.commit()
            try:
                await self._send(row)
            except RouteUnavailable as exc:
                if await self._move_to_fallback(row, exc, moment=moment):
                    result["fell_back"] += 1
                    result["retryable"] += 1
                else:
                    self._record_failure(row, exc, moment=moment)
                    result[row.status] += 1
            except Exception as exc:  # pragma: no cover - defensive
                row.status = "retryable"
                row.last_error = f"unexpected: {exc.__class__.__name__}"[:_MAX_ERROR_LENGTH]
                row.next_retry_at = moment + timedelta(minutes=1)
                result["retryable"] += 1
            else:
                row.status = "sent"
                row.sent_at = moment
                row.last_error = None
                row.next_retry_at = None
                result["sent"] += 1
            result["processed"] += 1
            await self.session.commit()
        return result

    async def _due_rows(
        self, *, limit: int, moment: datetime
    ) -> Sequence[OperationalAlertDelivery]:
        return list(
            (
                await self.session.scalars(
                    select(OperationalAlertDelivery)
                    .where(
                        OperationalAlertDelivery.status.in_({"pending", "retryable"}),
                        or_(
                            OperationalAlertDelivery.next_retry_at.is_(None),
                            OperationalAlertDelivery.next_retry_at <= moment,
                        ),
                    )
                    .order_by(OperationalAlertDelivery.created_at.asc())
                    .limit(min(max(limit, 1), 100))
                )
            ).all()
        )

    async def _move_to_fallback(
        self,
        row: OperationalAlertDelivery,
        exc: RouteUnavailable,
        *,
        moment: datetime,
    ) -> bool:
        """Switch to the second route once, and say in the row that it happened."""

        if row.used_fallback or not row.fallback_route:
            return False
        if row.fallback_route not in DELIVERY_ROUTE_DEPENDENCIES:
            return False
        row.route = row.fallback_route
        row.used_fallback = True
        row.status = "retryable"
        row.last_error = f"{exc.code}: {exc}"[:_MAX_ERROR_LENGTH]
        # Immediately, not after a backoff. The primary has already refused, and the
        # fallback exists precisely for the minutes the primary is down.
        row.next_retry_at = moment
        return True

    def _record_failure(
        self,
        row: OperationalAlertDelivery,
        exc: RouteUnavailable,
        *,
        moment: datetime,
    ) -> None:
        exhausted = row.attempt_count >= self.settings.operational_alert_max_attempts
        row.status = "failed" if exhausted or not exc.retryable else "retryable"
        row.last_error = f"{exc.code}: {exc}"[:_MAX_ERROR_LENGTH]
        row.next_retry_at = (
            None if row.status == "failed" else moment + timedelta(minutes=2)
        )

    async def _send(self, row: OperationalAlertDelivery) -> None:
        payload = dict(row.payload or {})
        subject = str(payload.get("subject") or row.rule_name)
        body = str(payload.get("body") or "")
        if row.route == "ops_telegram":
            await self._send_telegram(subject, body)
        elif row.route == "ops_email":
            await self._send_email(subject, body)
        elif row.route == "system_brain":
            # The last resort. The issue queue already holds this alert with its full
            # history, and the admin health page reads it, so there is genuinely
            # nothing left to transmit — recording that this is where it ended is the
            # honest outcome, not a pretended send.
            logger.warning(
                "Operational alert %s recorded to the admin surface only", row.rule_name
            )
        else:
            raise RouteUnavailable(
                "unknown_route", f"Unknown delivery route {row.route!r}.", retryable=False
            )

    async def _send_telegram(self, subject: str, body: str) -> None:
        from ai_market_monitor.telegram.adapter import (
            TelegramDeliveryError,
            TelegramHttpAdapter,
        )
        from ai_market_monitor.telegram.types import TelegramOutboundMessage

        chat_id = (self.settings.operational_alert_telegram_chat_id or "").strip()
        if not chat_id:
            raise RouteUnavailable(
                "telegram_not_configured",
                "OPERATIONAL_ALERT_TELEGRAM_CHAT_ID is not set.",
                retryable=False,
            )
        if not self.settings.telegram_enabled or self.settings.telegram_bot_token is None:
            raise RouteUnavailable(
                "telegram_disabled",
                "Telegram is switched off in this deployment.",
                retryable=False,
            )
        try:
            await TelegramHttpAdapter(self.settings).deliver(
                TelegramOutboundMessage(chat_id=chat_id, text=f"{subject}\n\n{body}")
            )
        except TelegramDeliveryError as exc:
            raise RouteUnavailable(
                exc.code, str(exc), retryable=bool(getattr(exc, "retryable", True))
            ) from exc

    async def _send_email(self, subject: str, body: str) -> None:
        from ai_market_monitor.services.email_delivery import (
            AuthEmailService,
            EmailDeliveryError,
        )

        recipient = (self.settings.operational_alert_email or "").strip()
        if not recipient:
            raise RouteUnavailable(
                "email_not_configured",
                "OPERATIONAL_ALERT_EMAIL is not set.",
                retryable=False,
            )
        try:
            await AuthEmailService(self.settings).send_transactional(
                recipient=recipient,
                subject=subject,
                text_body=body,
                # Plain text on purpose. An operations page is read on a phone at
                # three in the morning; there is nothing to style.
                html_body=f"<pre>{escape(body)}</pre>",
                idempotency_key=subject,
                purpose="operational_alert",
            )
        except EmailDeliveryError as exc:
            raise RouteUnavailable(
                exc.code, str(exc), retryable=bool(getattr(exc, "retryable", True))
            ) from exc
