"""Alerts, delivered by email.

Email is a full delivery channel, not a copy of one. It carries the same alerts Telegram
carries — a Shariah status changing, a setup coming close, a setup completing, an
opportunity moving on, a check that could not run, an account notice — recorded in the
same ``alert_deliveries`` table and retried by the same machinery.

Two rules shape everything here:

**The words are not written here.** ``AlertPresentation`` is the one owner of what an
alert claims, and ``product_language.message_kind`` is the one owner of the plain words
for what kind of message it is. This file only decides the *layout*. A renderer that
read ``proof_receipt`` itself would be a second opinion about one piece of evidence, and
the two channels would eventually tell one person two different things about one event.

**A screening status is quoted, never concluded.** What appears in an email is the
status that was published and stored when the alert was raised. Nothing here infers one,
softens one, or lets an alert imply that a coin became permitted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    CandidateReadinessSnapshot,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    DeliveryChannel,
    DeliveryStatus,
    IdentityProvider,
)
from ai_market_monitor.services.alert_presentation import AlertPresentation
from ai_market_monitor.services.email_branding import (
    BrandedEmail,
    EmailLink,
    HilalMarketsEmailRenderer,
    button,
    check_list,
    divider,
    fact_table,
    lead,
    note,
    paragraph,
    status_block,
)
from ai_market_monitor.services.email_delivery import (
    AuthEmailService,
    EmailDeliveryError,
    email_delivery_available,
)
from ai_market_monitor.services.product_language import message_kind, opportunity_presentation
from ai_market_monitor.services.trials import TrialLifecycleService

#: How many attempts before an email delivery is given up on.
#:
#: The same five as Telegram. One number for "we tried enough" across the product, so a
#: person cannot find one channel still retrying an alert the other abandoned.
MAX_ATTEMPTS = 5

#: The address alerts are sent from.
#:
#: A no-reply address on purpose: an alert is a notification, and a reply to it would
#: land nowhere a person would ever be answered from. Support has its own route, which
#: the footer of every email points at.
ALERT_SENDER_EMAIL = "no-reply@hilalmarkets.com"
ALERT_SENDER_NAME = "Hilal Markets"


class AlertEmailRenderer:
    """One alert, laid out as an email. Every claim comes from the presentation."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.frame = HilalMarketsEmailRenderer(settings)

    def render(self, presentation: AlertPresentation) -> BrandedEmail:
        kind = message_kind(presentation.alert_type)
        subject = f"{kind.label}: {presentation.symbol}"
        if presentation.is_account_notice:
            return self._account_notice(presentation, kind, subject)
        return self._market_alert(presentation, kind, subject)

    # ── The two shapes an alert takes ────────────────────────────────────────

    def _account_notice(self, presentation, kind, subject: str) -> BrandedEmail:
        """A Shariah status change, or something about the account.

        These carry a written title and body rather than a set of readings, so the
        layout is the title, the words, the evidence link, and nothing invented.
        """

        actions = [action for action in presentation.actions if action.url]
        # The screening record, when this is a screening change. Every value is already
        # on the presentation; nothing here is read out of the proof a second time.
        record = _screening_rows(presentation) or _compliance_rows(presentation)
        text_body = (
            f"{presentation.title}\n\n"
            f"{presentation.body}\n\n"
            + "".join(f"  {label}: {_plain(value)}\n" for label, value in record)
            + ("\n" if record else "")
            + "".join(f"{action.label}: {action.url}\n" for action in actions)
            + "\nHilal Markets screens and monitors. It does not execute trades and does "
            "not tell you what to buy."
        )
        content = (
            status_block(tone=kind.semantic_status, label=kind.label, meaning=kind.meaning)
            # No repeat of the title here: the frame has already said it, in larger type,
            # directly above. Saying it twice is the beginning of a wall of text.
            + paragraph(presentation.body)
            + fact_table(record, title="What the record says")
            + "".join(
                button(action.label, str(action.url), secondary=index > 0)
                for index, action in enumerate(actions)
            )
            + note(
                "A screening status comes only from a recorded review with its evidence. "
                "It is never decided by a price, by a model, or by this message."
            )
        )
        return BrandedEmail(
            subject=subject,
            text_body=text_body,
            html_body=self.frame.shell(
                title=presentation.title,
                eyebrow=kind.category,
                preheader=_first_sentence(presentation.body),
                content_html=content,
                footer_reason=(
                    "You are receiving this because you chose email for Hilal Markets "
                    "notifications. You can change that on your Connections page."
                ),
            ),
        )

    def _market_alert(self, presentation, kind, subject: str) -> BrandedEmail:
        """A setup on one of the person's own lists.

        The order answers the three questions somebody actually asks, in order: what
        happened, on what, and what did you actually see.
        """

        checks = [
            (condition.name, True, _reading(condition))
            for condition in presentation.passed_conditions
        ] + [
            (condition.name, False, _reading(condition))
            for condition in presentation.missing_conditions
        ]
        trade_context = presentation.trade_context_facts()
        actions = [action for action in presentation.actions if action.url]

        # The plain-text part says the same things in the same words as the HTML part.
        # It deliberately is *not* `telegram_text()`: that is written for a chat window
        # and carries words from inside the machine — "[PASS]", "Lifecycle", "Strategy
        # version" — which a beginner reading their email has no way to interpret. The
        # facts are identical because both come from the presentation; only the layout
        # differs, which is the whole point of having the facts owned in one place.
        text_body = _plain_text(presentation, kind, checks, trade_context, actions)
        content = (
            status_block(tone=kind.semantic_status, label=kind.label, meaning=kind.meaning)
            + lead(f"{presentation.symbol} — on your Watchlist “{presentation.strategy}”.")
            + fact_table(_email_facts(presentation), title="What was watched")
            + check_list(checks, title="What we saw")
            + (
                fact_table(
                    [(fact.label, fact.value) for fact in trade_context],
                    title="The entry, stop and targets you set",
                )
                if trade_context
                else status_block(
                    tone="neutral",
                    label="Research only",
                    meaning=(
                        "You set no entry, stop or target for this one, so none is shown. "
                        "Nothing has been filled in for you."
                    ),
                )
            )
            + fact_table(
                _screening_rows(presentation),
                title="Screening status when this was checked",
            )
            + divider()
            + "".join(
                button(action.label, str(action.url), secondary=index > 0)
                for index, action in enumerate(actions)
            )
            + note(
                "These are your own conditions, and this is a record of what the market "
                "was doing when we looked. It is not advice, and Hilal Markets never "
                "buys or sells anything."
            )
        )
        return BrandedEmail(
            subject=subject,
            text_body=text_body,
            html_body=self.frame.shell(
                title=f"{presentation.symbol}: {kind.label.lower()}",
                eyebrow=kind.category,
                preheader=kind.meaning,
                content_html=content,
                footer_reason=(
                    "You are receiving this because you chose email for Hilal Markets "
                    "notifications. You can change that on your Connections page."
                ),
            ),
        )


def _compliance_rows(presentation: AlertPresentation) -> list[tuple[str, str | EmailLink]]:
    """What a screening change actually changed, when there is no full screening block.

    A screening-change alert carries its record in different fields from a market alert
    — the coin, the status it moved to, when it was reviewed, and under which
    methodology. Without this the email said a status had changed and then showed
    nothing about it, which is the shape of an alert nobody can act on.

    Every value comes off the presentation. Nothing is read out of the proof again, and
    nothing is filled in: a field the record does not carry is left out.
    """

    if presentation.alert_type != AlertType.COMPLIANCE.value:
        return []
    rows: list[tuple[str, str | EmailLink]] = [("Coin", presentation.symbol)]
    if presentation.lifecycle_state not in {"", "n/a"}:
        rows.append(("New status", presentation.lifecycle_state.replace("_", " ")))
    if presentation.data_freshness not in {"", "n/a"}:
        rows.append(("Reviewed on", presentation.data_freshness))
    if presentation.strategy_version not in {None, "", "n/a"}:
        rows.append(("Methodology version", str(presentation.strategy_version)))
    # No Passport row. The first button on this email already goes there, and a table
    # row pointing at the same place is one extra thing to read for nothing.
    return rows


def _plain(value: str | EmailLink) -> str:
    """A table value as plain text, for the part of the email that has no HTML."""

    return f"{value.label} — {value.url}" if isinstance(value, EmailLink) else value


def _screening_rows(presentation: AlertPresentation) -> list[tuple[str, str | EmailLink]]:
    """The stored screening status, with its evidence as something to press.

    The Passport arrives as a raw address, which is right for a chat message and wrong
    for an email: nobody reads a URL to learn it is the Passport, and one unbreakable
    address is what pushed this email sideways on a phone. Only the layout changes —
    the address itself still comes from the presentation.
    """

    rows: list[tuple[str, str | EmailLink]] = []
    for fact in presentation.screening_facts():
        if fact.key == "passport_url" and fact.value.startswith("https://"):
            rows.append((fact.label, EmailLink("Open the Evidence Passport", fact.value)))
        else:
            rows.append((fact.label, fact.value))
    return rows


def _email_facts(presentation: AlertPresentation) -> list[tuple[str, str | EmailLink]]:
    """The alert's facts, with every stored word turned into a readable one.

    Only one value needs it: the stage is stored as a state name such as
    ``near_confirmation``, which means nothing to a beginner. What that state means in
    plain words already has an owner — ``opportunity_presentation`` — so it is resolved
    there rather than this file growing a second set of words for the same states.
    """

    rows: list[tuple[str, str | EmailLink]] = []
    for fact in presentation.key_facts():
        value = fact.value
        if fact.key == "stage":
            value = opportunity_presentation(value).label
        rows.append((fact.label, value))
    return rows


def _plain_text(presentation, kind, checks, trade_context, actions) -> str:
    """The same email, for a reader with no HTML. Same claims, same order, same words."""

    lines = [
        kind.label,
        kind.meaning,
        "",
        f"{presentation.symbol} — on your Watchlist “{presentation.strategy}”.",
        "",
        "What was watched",
    ]
    lines += [f"  {label}: {value}" for label, value in _email_facts(presentation)]
    lines += ["", "What we saw"]
    for name, passed, detail in checks:
        word = "Met" if passed else "Not met yet"
        lines.append(f"  {name} — {word}." + (f" {detail}" if detail else ""))
    if trade_context:
        lines += ["", "The entry, stop and targets you set"]
        lines += [f"  {fact.label}: {fact.value}" for fact in trade_context]
    else:
        lines += [
            "",
            "Research only. You set no entry, stop or target for this one, so none is "
            "shown. Nothing has been filled in for you.",
        ]
    screening = presentation.screening_facts()
    if screening:
        lines += ["", "Screening status when this was checked"]
        lines += [f"  {fact.label}: {fact.value}" for fact in screening]
    if actions:
        lines += [""]
        lines += [f"{action.label}: {action.url}" for action in actions]
    lines += [
        "",
        "These are your own conditions, and this is a record of what the market was "
        "doing when we looked. It is not advice, and Hilal Markets never buys or sells "
        "anything.",
    ]
    return "\n".join(lines)


def _reading(condition) -> str:
    """What was asked for and what was read, or silence when neither is recorded.

    A missing half is never filled in with a zero. "We saw 0" about a value nobody
    managed to read is a false statement that somebody could act on.
    """

    wanted = condition.required_value
    actual = condition.actual_value
    parts = []
    if wanted is not None:
        parts.append(f"You asked for {wanted}.")
    if actual is not None:
        parts.append(f"We saw {actual}.")
    elif wanted is not None:
        parts.append("We could not read it.")
    return " ".join(parts)


def _first_sentence(text: str) -> str:
    body = " ".join(str(text or "").split())
    head, stop, _rest = body.partition(". ")
    return (head + stop).strip() or body[:120]


class EmailAlertDeliveryService:
    """Send the email deliveries that are due, and record what happened.

    Deliberately the same shape as ``TelegramDeliveryService``: pick up what is pending
    or due for retry, attempt it, and write the outcome onto the delivery row. That is
    what makes email a real channel rather than a side path — the same retry, the same
    failure codes, and the same trail behind "why was I not told".
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        email: AuthEmailService | None = None,
    ):
        self.session = session
        self.settings = settings
        self.email = email or AuthEmailService(settings)
        self.renderer = AlertEmailRenderer(settings)

    async def process_due(self, *, limit: int = 50) -> list[AlertDelivery]:
        if not email_delivery_available(self.settings):
            return []
        now = datetime.now(UTC)
        deliveries = (
            await self.session.scalars(
                select(AlertDelivery)
                .where(
                    AlertDelivery.channel == DeliveryChannel.EMAIL,
                    or_(
                        AlertDelivery.status == DeliveryStatus.PENDING,
                        (
                            (AlertDelivery.status == DeliveryStatus.FAILED_RETRYABLE)
                            & (AlertDelivery.next_retry_at <= now)
                        ),
                    ),
                )
                .order_by(AlertDelivery.created_at.asc())
                .limit(limit)
            )
        ).all()
        processed: list[AlertDelivery] = []
        for delivery in deliveries:
            processed.append(await self._deliver_one(delivery, now))
        await self.session.flush()
        return processed

    async def _deliver_one(self, delivery: AlertDelivery, now: datetime) -> AlertDelivery:
        alert = await self.session.get(Alert, delivery.alert_id)
        recipient = delivery.destination_key.removeprefix("email:")
        if alert is None or not delivery.destination_key.startswith("email:") or not recipient:
            delivery.status = DeliveryStatus.FAILED_PERMANENT
            delivery.last_error_code = "delivery_context_missing"
            return delivery

        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        try:
            rendered = self.renderer.render(
                AlertPresentation.from_alert(
                    alert, public_base_url=str(self.settings.public_base_url)
                )
            )
            message_id = await self.email.send_transactional(
                recipient=recipient,
                subject=rendered.subject,
                text_body=rendered.text_body,
                html_body=rendered.html_body,
                # One key per alert per address, so a retry cannot become a second copy
                # of the same alert in somebody's inbox.
                idempotency_key=f"alert-{alert.id}",
                purpose="alert",
                sender_email=ALERT_SENDER_EMAIL,
                sender_name=ALERT_SENDER_NAME,
            )
            delivery.status = DeliveryStatus.SENT
            delivery.provider_message_id = message_id
            delivery.delivered_at = now
            delivery.next_retry_at = None
            delivery.last_error_code = None
            delivery.last_error_detail = None
            await TrialLifecycleService(self.session, self.settings).record_successful_delivery(
                delivery
            )
            await self._mark_readiness(alert, "delivered")
        except EmailDeliveryError as exc:
            permanent = not exc.retryable or delivery.attempt_count >= MAX_ATTEMPTS
            delivery.status = (
                DeliveryStatus.FAILED_PERMANENT if permanent else DeliveryStatus.FAILED_RETRYABLE
            )
            delivery.delivered_at = None
            delivery.last_error_code = exc.code
            # Truncated, never raised on. A field cap firing while the platform is
            # reporting a problem turns a diagnostic into the failure.
            delivery.last_error_detail = str(exc)[:500]
            await self._mark_readiness(alert, "failed")
            if not permanent:
                delivery.next_retry_at = now + timedelta(
                    seconds=min(3600, 30 * (2 ** (delivery.attempt_count - 1)))
                )
        return delivery

    async def _mark_readiness(self, alert: Alert, state: str) -> None:
        if alert.setup_instance_id is None:
            return
        readiness = await self.session.scalar(
            select(CandidateReadinessSnapshot).where(
                CandidateReadinessSnapshot.setup_instance_id == alert.setup_instance_id
            )
        )
        if readiness is not None:
            readiness.notification_status = state


async def alert_email_address(session: AsyncSession, user_id: UUID) -> str | None:
    """Where an alert email goes: the **verified** address on the account, and nowhere else.

    Three decisions live here so that nothing else has to make them again:

    * *Which address.* The account's own, never a second "notifications email". A
      separate field is one more thing to mistype, and a typo there is silent — the
      person simply stops hearing anything and cannot tell that from "nothing happened".
    * *Verified only.* An unverified address is an address somebody typed, not one they
      have been shown to control. Alerts name the coins a person watches, so sending
      them to an unproven address would hand their activity to whoever owns it.
    * *Which one, when there are several.* The primary first, then the oldest — the same
      order `account_admin` already uses, so "the account's email address" means one
      thing across the product.
    """

    return await session.scalar(
        select(UserIdentity.normalized_identifier)
        .where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == IdentityProvider.EMAIL,
            UserIdentity.is_verified.is_(True),
            UserIdentity.normalized_identifier.is_not(None),
        )
        .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
        .limit(1)
    )
