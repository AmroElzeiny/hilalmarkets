from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PLAN_DEFINITIONS
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    AuditEvent,
    Plan,
    ScanJob,
    Strategy,
    Subscription,
    TelegramConnection,
    Trial,
    TrialAlertAttribution,
    TrialCycle,
    User,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConnectionStatus,
    DeliveryStatus,
    StrategyStatus,
    SubscriptionStatus,
    TrialStatus,
    UserStatus,
)
from ai_market_monitor.engine.dedup import stable_event_hash
from ai_market_monitor.services.entitlements import PlanCatalogService
from ai_market_monitor.services.monitor_scan_state import CHECK_FINISHED_STATUSES

#: How many deliveries the reconcile job holds at once.
#:
#: It still visits every delivery — it simply does so a page at a time, so the memory it
#: needs stays the same whether the product has sent a thousand notifications or a million.
DELIVERY_RECONCILE_BATCH = 500

QUALIFYING_ALERT_TYPES = {
    AlertType.FORMING,
    AlertType.NEAR_MISS,
    AlertType.CONFIRMED,
    AlertType.LIFECYCLE,
}


class TrialError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TrialReminder:
    user_id: UUID
    trial_id: UUID
    reminder_type: str
    days_remaining: int
    ends_at: datetime


class TrialLifecycleService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def activate(self, user_id: UUID) -> Trial:
        existing = await self.session.scalar(select(Trial).where(Trial.user_id == user_id))
        if existing is not None:
            if existing.status in {
                TrialStatus.ELIGIBLE,
                TrialStatus.ACTIVE,
                TrialStatus.ACTIVATED,
                TrialStatus.ENDING_SOON,
                TrialStatus.MANUALLY_EXTENDED,
            }:
                return existing
            if existing.status in {
                TrialStatus.CONVERTED,
                TrialStatus.EXPIRED,
                TrialStatus.CANCELED,
                TrialStatus.BLOCKED,
            }:
                raise TrialError(
                    "trial_already_used",
                    "This verified identity has already used or been blocked from a trial.",
                )
        plan = await PlanCatalogService(self.session).get_or_sync("pro_trial")
        now = datetime.now(UTC)
        trial = Trial(
            user_id=user_id,
            plan_id=plan.id,
            status=TrialStatus.ELIGIBLE,
            starts_at=now,
            ends_at=now,
        )
        self.session.add(trial)
        self._audit(
            user_id,
            "trial.eligible",
            "trial",
            None,
            {"plan": "pro_trial", "cycle_days": self.settings.trial_days},
        )
        await self.session.flush()
        return trial

    async def start_monitoring_cycle(
        self, user_id: UUID, *, activated_at: datetime | None = None
    ) -> Trial | None:
        trial = await self.session.scalar(select(Trial).where(Trial.user_id == user_id))
        if trial is None:
            return None
        if trial.status in {TrialStatus.CONVERTED, TrialStatus.EXPIRED, TrialStatus.CANCELED}:
            return trial
        active_cycle = await self.current_cycle(trial.id)
        if active_cycle is not None:
            return trial
        now = activated_at or datetime.now(UTC)
        latest_cycle_number = await self.session.scalar(
            select(func.max(TrialCycle.cycle_number)).where(TrialCycle.trial_id == trial.id)
        )
        cycle = TrialCycle(
            trial_id=trial.id,
            cycle_number=int(latest_cycle_number or 0) + 1,
            starts_at=now,
            ends_at=now + timedelta(days=self.settings.trial_days),
            status="active",
            successful_scan_coverage=Decimal("0"),
        )
        self.session.add(cycle)
        trial.status = TrialStatus.ACTIVE
        trial.starts_at = now if latest_cycle_number is None else trial.starts_at
        trial.ends_at = cycle.ends_at
        self._audit(
            user_id,
            "trial.cycle_started",
            "trial",
            trial.id,
            {"cycle_number": cycle.cycle_number, "ends_at": cycle.ends_at.isoformat()},
        )
        await self.session.flush()
        await self._create_trial_message(
            user_id=user_id,
            cycle=cycle,
            message_type="cycle_started",
            title="Monitor trial started",
            body=(
                f"Your {self.settings.trial_days}-day Monitor trial has started. "
                "No payment method is required."
            ),
        )
        return trial

    async def current_cycle(
        self, trial_id: UUID, *, at: datetime | None = None
    ) -> TrialCycle | None:
        now = at or datetime.now(UTC)
        return await self.session.scalar(
            select(TrialCycle)
            .where(
                TrialCycle.trial_id == trial_id,
                TrialCycle.status == "active",
                TrialCycle.starts_at <= now,
                TrialCycle.ends_at > now,
            )
            .order_by(TrialCycle.cycle_number.desc())
        )

    async def active_cycle_for_user(
        self, user_id: UUID, *, at: datetime | None = None
    ) -> TrialCycle | None:
        now = at or datetime.now(UTC)
        return await self.session.scalar(
            select(TrialCycle)
            .join(Trial, TrialCycle.trial_id == Trial.id)
            .where(
                Trial.user_id == user_id,
                Trial.status.in_(
                    [
                        TrialStatus.ACTIVE,
                        TrialStatus.ENDING_SOON,
                        TrialStatus.MANUALLY_EXTENDED,
                    ]
                ),
                TrialCycle.status == "active",
                TrialCycle.starts_at <= now,
                TrialCycle.ends_at > now,
            )
            .order_by(TrialCycle.cycle_number.desc())
        )

    async def trial_alert_cap_reached(self, user_id: UUID) -> tuple[bool, TrialCycle | None, int]:
        now = datetime.now(UTC)
        active_subscription = await self.session.scalar(
            select(Subscription.id)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.current_period_end > now,
            )
            .limit(1)
        )
        if active_subscription is not None:
            return False, None, 0
        trial = await self.session.scalar(select(Trial).where(Trial.user_id == user_id))
        if trial is None:
            return False, None, 0
        cycle = await self.current_cycle(trial.id)
        cap = self.settings.trial_alerts_per_cycle
        plan = await self.session.get(Plan, trial.plan_id)
        if plan is not None:
            central_definition = PLAN_DEFINITIONS.get(plan.code)
            if central_definition is not None:
                cap = int(
                    central_definition.limits.get("alerts_per_trial_cycle")
                    or self.settings.trial_alerts_per_cycle
                )
            else:
                cap = int(
                    (plan.features.get("limits") or {}).get("alerts_per_trial_cycle")
                    or self.settings.trial_alerts_per_cycle
                )
        if cycle is None:
            return False, None, cap
        if cap <= 0:
            return False, cycle, cap
        return cycle.qualifying_alerts_delivered >= cap, cycle, cap

    async def record_alert_generated(
        self, alert: Alert, *, suppressed_reason: str | None = None
    ) -> TrialAlertAttribution | None:
        if alert.alert_type not in QUALIFYING_ALERT_TYPES:
            return None
        cycle = await self.active_cycle_for_user(alert.user_id, at=alert.created_at)
        if cycle is None:
            return None
        existing = await self.session.scalar(
            select(TrialAlertAttribution).where(
                TrialAlertAttribution.trial_cycle_id == cycle.id,
                TrialAlertAttribution.alert_id == alert.id,
            )
        )
        if existing:
            return existing
        attribution = TrialAlertAttribution(
            trial_cycle_id=cycle.id,
            alert_id=alert.id,
            first_successful_delivery_id=None,
            qualification_status="suppressed" if suppressed_reason else "generated",
            qualification_reason=suppressed_reason or "awaiting_successful_delivery",
            attributed_at=datetime.now(UTC),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(attribution)
                if suppressed_reason:
                    cycle.suppressed_alert_count += 1
                else:
                    cycle.qualifying_alerts_generated += 1
                await self.session.flush()
        except IntegrityError:
            return await self.session.scalar(
                select(TrialAlertAttribution).where(
                    TrialAlertAttribution.trial_cycle_id == cycle.id,
                    TrialAlertAttribution.alert_id == alert.id,
                )
            )
        return attribution

    async def record_successful_delivery(
        self, delivery: AlertDelivery
    ) -> TrialAlertAttribution | None:
        if delivery.status not in {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}:
            return None
        alert = await self.session.get(Alert, delivery.alert_id)
        if alert is None or alert.alert_type not in QUALIFYING_ALERT_TYPES:
            return None
        cycle = await self.active_cycle_for_user(alert.user_id, at=alert.created_at)
        if cycle is None:
            return None
        existing = await self.session.scalar(
            select(TrialAlertAttribution).where(
                TrialAlertAttribution.trial_cycle_id == cycle.id,
                TrialAlertAttribution.alert_id == alert.id,
            )
        )
        if existing and existing.qualification_status == "qualifying_delivered":
            return existing
        if existing and existing.qualification_status == "suppressed":
            return existing
        first_qualifying_delivery = cycle.qualifying_alerts_delivered == 0
        if existing is None:
            existing = TrialAlertAttribution(
                trial_cycle_id=cycle.id,
                alert_id=alert.id,
                first_successful_delivery_id=delivery.id,
                qualification_status="qualifying_delivered",
                qualification_reason="successful_live_delivery",
                attributed_at=datetime.now(UTC),
            )
            self.session.add(existing)
            cycle.qualifying_alerts_generated += 1
        else:
            existing.first_successful_delivery_id = delivery.id
            existing.qualification_status = "qualifying_delivered"
            existing.qualification_reason = "successful_live_delivery"
            existing.attributed_at = datetime.now(UTC)
        cycle.qualifying_alerts_delivered += 1
        await self.session.flush()
        if first_qualifying_delivery:
            await self._create_trial_message(
                user_id=alert.user_id,
                cycle=cycle,
                message_type="first_qualifying_alert_delivered",
                title="First qualifying alert delivered",
                body=(
                    "A live setup alert was successfully delivered during this trial cycle. "
                    "This cycle will end normally on its scheduled date unless you upgrade."
                ),
            )
        return existing

    async def evaluate_due_cycles(self, *, now: datetime | None = None) -> list[Trial]:
        current_time = now or datetime.now(UTC)
        settlement_cutoff = current_time - timedelta(
            minutes=self.settings.delivery_settlement_grace_minutes
        )
        cycles = (
            await self.session.scalars(
                select(TrialCycle)
                .join(Trial, TrialCycle.trial_id == Trial.id)
                .where(
                    Trial.status.in_(
                        [
                            TrialStatus.ACTIVE,
                            TrialStatus.ENDING_SOON,
                            TrialStatus.MANUALLY_EXTENDED,
                        ]
                    ),
                    TrialCycle.status == "active",
                    TrialCycle.ends_at <= settlement_cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        affected: list[Trial] = []
        for cycle in cycles:
            trial = await self.session.get(Trial, cycle.trial_id)
            if trial is None:
                continue
            await self._evaluate_cycle(trial, cycle, current_time)
            affected.append(trial)

        legacy_trials = (
            await self.session.scalars(
                select(Trial).where(
                    Trial.status.in_(
                        [
                            TrialStatus.ACTIVE,
                            TrialStatus.ACTIVATED,
                            TrialStatus.ENDING_SOON,
                            TrialStatus.MANUALLY_EXTENDED,
                        ]
                    ),
                    Trial.ends_at <= current_time,
                    ~select(TrialCycle.id).where(TrialCycle.trial_id == Trial.id).exists(),
                )
            )
        ).all()
        for trial in legacy_trials:
            trial.status = TrialStatus.EXPIRED
            self._audit(
                trial.user_id,
                "trial.expired",
                "trial",
                trial.id,
                {"ended_at": trial.ends_at.isoformat(), "legacy": True},
            )
            affected.append(trial)
        await self.session.flush()
        return affected

    async def expire_due(self, *, now: datetime | None = None) -> list[Trial]:
        return await self.evaluate_due_cycles(now=now)

    async def mark_ending_soon(self, *, now: datetime | None = None) -> list[Trial]:
        current_time = now or datetime.now(UTC)
        cutoff = current_time + timedelta(days=3)
        trials = (
            await self.session.scalars(
                select(Trial).where(
                    Trial.status == TrialStatus.ACTIVE,
                    Trial.ends_at <= cutoff,
                    Trial.ends_at > current_time,
                )
            )
        ).all()
        for trial in trials:
            trial.status = TrialStatus.ENDING_SOON
            self._audit(
                trial.user_id,
                "trial.ending_soon",
                "trial",
                trial.id,
                {"ends_at": trial.ends_at.isoformat()},
            )
        await self.session.flush()
        return list(trials)

    async def create_due_reminder_messages(self, *, now: datetime | None = None) -> list[Alert]:
        current_time = now or datetime.now(UTC)
        active_trials = (
            await self.session.scalars(
                select(Trial).where(
                    Trial.status.in_([TrialStatus.ACTIVE, TrialStatus.ENDING_SOON]),
                    Trial.ends_at > current_time,
                )
            )
        ).all()
        alerts: list[Alert] = []
        for trial in active_trials:
            trial_ends_at = _as_aware(trial.ends_at)
            remaining = trial_ends_at - current_time
            days_remaining = max(0, remaining.days)
            if days_remaining not in {1, 3, 7}:
                continue
            already_sent_today = (
                trial.reminder_sent_at is not None
                and trial.reminder_sent_at.date() == current_time.date()
            )
            if already_sent_today:
                continue
            cycle = await self.current_cycle(trial.id, at=current_time)
            if cycle is None:
                continue
            title = "Trial ending" if days_remaining == 1 else f"{days_remaining} days remaining"
            alert = await self._create_trial_message(
                user_id=trial.user_id,
                cycle=cycle,
                message_type=f"{days_remaining}_day_remaining",
                title=title,
                body=(
                    f"Your current trial monitoring cycle ends at "
                    f"{trial_ends_at.isoformat()}. Upgrade when you are ready; the platform "
                    "does not place trades for you."
                ),
            )
            if alert is not None:
                alerts.append(alert)
        await self.session.flush()
        return alerts

    async def reminders_due(self, *, now: datetime | None = None) -> list[TrialReminder]:
        alerts = await self.create_due_reminder_messages(now=now)
        reminders: list[TrialReminder] = []
        for alert in alerts:
            proof = alert.proof_receipt or {}
            if not str(proof.get("trial_message_type", "")).endswith("_day_remaining"):
                continue
            reminders.append(
                TrialReminder(
                    user_id=alert.user_id,
                    trial_id=UUID(str(proof["trial_id"])),
                    reminder_type=str(proof["trial_message_type"]),
                    days_remaining=int(proof.get("days_remaining") or 0),
                    ends_at=datetime.fromisoformat(str(proof["cycle_ends_at"])),
                )
            )
        return reminders

    async def reconcile_alert_deliveries(self, *, now: datetime | None = None) -> dict[str, int]:
        reminders_marked = 0
        qualifying_reconciled = 0
        # Every delivery ever made is still visited, but a page at a time. Asking for them
        # all in one answer meant this job's memory grew with the product's whole history,
        # and it carried each alert's `proof_receipt` blob along with it — the receipt is
        # the only part of the alert this needs, so it is the only part it asks for.
        after_id: UUID | None = None
        while True:
            statement = (
                select(AlertDelivery, Alert.proof_receipt, Alert.created_at)
                .join(Alert, Alert.id == AlertDelivery.alert_id)
                .where(AlertDelivery.status.in_([DeliveryStatus.SENT, DeliveryStatus.DELIVERED]))
                .order_by(AlertDelivery.id)
                .limit(DELIVERY_RECONCILE_BATCH)
            )
            if after_id is not None:
                statement = statement.where(AlertDelivery.id > after_id)
            rows = (await self.session.execute(statement)).all()
            if not rows:
                break
            for delivery, proof_receipt, alert_created_at in rows:
                proof = proof_receipt or {}
                message_type = str(proof.get("trial_message_type") or "")
                if message_type.endswith("_day_remaining") or message_type == "trial_ending":
                    trial_id = proof.get("trial_id")
                    if trial_id is None:
                        continue
                    delivered_at = (
                        delivery.delivered_at or delivery.last_attempt_at or alert_created_at
                    )
                    trial = await self.session.get(Trial, UUID(str(trial_id)))
                    if trial is not None and (
                        trial.reminder_sent_at is None or trial.reminder_sent_at < delivered_at
                    ):
                        trial.reminder_sent_at = delivered_at
                        reminders_marked += 1
                attribution = await self.record_successful_delivery(delivery)
                if attribution is not None:
                    qualifying_reconciled += 1
            after_id = rows[-1][0].id
        await self.session.flush()
        return {
            "reminders_marked_sent": reminders_marked,
            "qualifying_alerts_reconciled": qualifying_reconciled,
        }

    async def repair_cycle_counters(self) -> dict[str, int]:
        cycles = (await self.session.scalars(select(TrialCycle))).all()
        repaired = 0
        for cycle in cycles:
            generated = await self.session.scalar(
                select(func.count(TrialAlertAttribution.id)).where(
                    TrialAlertAttribution.trial_cycle_id == cycle.id,
                    TrialAlertAttribution.qualification_status.in_(
                        ["generated", "qualifying_delivered"]
                    ),
                )
            )
            delivered = await self.session.scalar(
                select(func.count(TrialAlertAttribution.id)).where(
                    TrialAlertAttribution.trial_cycle_id == cycle.id,
                    TrialAlertAttribution.qualification_status == "qualifying_delivered",
                )
            )
            suppressed = await self.session.scalar(
                select(func.count(TrialAlertAttribution.id)).where(
                    TrialAlertAttribution.trial_cycle_id == cycle.id,
                    TrialAlertAttribution.qualification_status == "suppressed",
                )
            )
            new_values = (int(generated or 0), int(delivered or 0), int(suppressed or 0))
            old_values = (
                cycle.qualifying_alerts_generated,
                cycle.qualifying_alerts_delivered,
                cycle.suppressed_alert_count,
            )
            if new_values != old_values:
                (
                    cycle.qualifying_alerts_generated,
                    cycle.qualifying_alerts_delivered,
                    cycle.suppressed_alert_count,
                ) = new_values
                repaired += 1
        await self.session.flush()
        return {"cycles_repaired": repaired}

    async def convert(self, user_id: UUID, subscription_id: UUID) -> Trial | None:
        trial = await self.session.scalar(select(Trial).where(Trial.user_id == user_id))
        if trial is None:
            return None
        trial.status = TrialStatus.CONVERTED
        trial.converted_subscription_id = subscription_id
        now = datetime.now(UTC)
        active_cycles = (
            await self.session.scalars(
                select(TrialCycle).where(
                    TrialCycle.trial_id == trial.id,
                    TrialCycle.status == "active",
                )
            )
        ).all()
        for cycle in active_cycles:
            cycle.status = "converted"
            cycle.renewal_decision = "converted_to_paid"
            cycle.evaluated_at = now
            cycle.closed_at = now
        self._audit(
            user_id,
            "trial.converted",
            "trial",
            trial.id,
            {"subscription_id": str(subscription_id)},
        )
        await self.session.flush()
        if active_cycles:
            await self._create_trial_message(
                user_id=user_id,
                cycle=active_cycles[0],
                message_type="trial_converted",
                title="Trial converted",
                body="Your paid subscription is active. Trial limits no longer apply.",
            )
        return trial

    async def extend(
        self,
        user_id: UUID,
        *,
        days: int,
        admin_user_id: UUID,
        reason: str,
    ) -> Trial:
        if days <= 0:
            raise TrialError("invalid_extension", "Trial extension days must be positive.")
        trial = await self.session.scalar(select(Trial).where(Trial.user_id == user_id))
        if trial is None:
            plan = await PlanCatalogService(self.session).get_or_sync("pro_trial")
            now = datetime.now(UTC)
            trial = Trial(
                user_id=user_id,
                plan_id=plan.id,
                status=TrialStatus.MANUALLY_EXTENDED,
                starts_at=now,
                ends_at=now + timedelta(days=days),
            )
            self.session.add(trial)
        else:
            base = max(datetime.now(UTC), _as_aware(trial.ends_at))
            trial.status = TrialStatus.MANUALLY_EXTENDED
            trial.ends_at = base + timedelta(days=days)
        self.session.add(
            AuditEvent(
                actor_user_id=admin_user_id,
                actor_type="admin",
                action="trial.manually_extended",
                target_type="user",
                target_id=str(user_id),
                metadata_redacted={"days": days, "reason": reason},
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return trial

    async def _evaluate_cycle(
        self, trial: Trial, cycle: TrialCycle, current_time: datetime
    ) -> None:
        await self._fill_cycle_metrics(trial, cycle)
        cycle.evaluated_at = current_time
        if await self._has_paid_subscription(trial.user_id):
            trial.status = TrialStatus.CONVERTED
            cycle.status = "converted"
            cycle.renewal_decision = "converted_to_paid"
            cycle.closed_at = current_time
            return
        trial.status = TrialStatus.EXPIRED
        trial.ends_at = cycle.ends_at
        cycle.status = "expired"
        cycle.renewal_decision = "trial_period_completed"
        cycle.renewal_reason = "seven_day_monitor_trial_completed"
        cycle.closed_at = current_time
        self._audit(
            trial.user_id,
            "trial.expired",
            "trial_cycle",
            cycle.id,
            {
                "reason": cycle.renewal_reason,
                "qualifying_alerts_delivered": cycle.qualifying_alerts_delivered,
            },
        )

    async def _fill_cycle_metrics(self, trial: Trial, cycle: TrialCycle) -> None:
        cycle_length = max(0, int((cycle.ends_at - cycle.starts_at).total_seconds()))
        active_monitors = await self.session.scalar(
            select(func.count(Strategy.id)).where(
                Strategy.user_id == trial.user_id,
                Strategy.status == StrategyStatus.ACTIVE,
                Strategy.activated_at.is_not(None),
                Strategy.activated_at <= cycle.ends_at,
            )
        )
        notification_ready = await self.session.scalar(
            select(func.count(TelegramConnection.id)).where(
                TelegramConnection.user_id == trial.user_id,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
                TelegramConnection.alerts_enabled.is_(True),
                TelegramConnection.chat_id.is_not(None),
            )
        )
        cycle.active_monitor_duration_seconds = cycle_length if active_monitors else 0
        cycle.notification_ready_duration_seconds = cycle_length if notification_ready else 0
        total_jobs = await self.session.scalar(
            select(func.count(ScanJob.id))
            .join(Strategy, Strategy.active_version_id == ScanJob.strategy_version_id)
            .where(
                Strategy.user_id == trial.user_id,
                ScanJob.scheduled_for >= cycle.starts_at,
                ScanJob.scheduled_for < cycle.ends_at,
            )
        )
        successful_jobs = await self.session.scalar(
            select(func.count(ScanJob.id))
            .join(Strategy, Strategy.active_version_id == ScanJob.strategy_version_id)
            .where(
                Strategy.user_id == trial.user_id,
                ScanJob.scheduled_for >= cycle.starts_at,
                ScanJob.scheduled_for < cycle.ends_at,
                # The same list the dashboard reads, imported rather than repeated. Two
                # copies of "which states mean the market was really read" is how one
                # screen counted a canceled job as a check and the next one did not.
                ScanJob.status.in_(CHECK_FINISHED_STATUSES),
            )
        )
        if total_jobs:
            coverage = Decimal(successful_jobs or 0) / Decimal(total_jobs)
        else:
            coverage = Decimal("0")
        cycle.successful_scan_coverage = coverage.quantize(Decimal("0.00001"))

    async def _eligible_for_auto_renewal(self, trial: Trial, cycle: TrialCycle) -> bool:
        user = await self.session.get(User, trial.user_id)
        if user is not None and user.status == UserStatus.SUSPENDED:
            cycle.renewal_reason = "account_suspended"
            return False
        if trial.status == TrialStatus.BLOCKED:
            cycle.renewal_reason = "trial_blocked"
            return False
        if cycle.active_monitor_duration_seconds <= 0:
            cycle.renewal_reason = "no_monitor_activated"
            return False
        if cycle.notification_ready_duration_seconds <= 0:
            cycle.renewal_reason = "notification_channel_not_ready"
            return False
        return True

    async def _has_paid_subscription(self, user_id: UUID) -> bool:
        subscription_count = await self.session.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.user_id == user_id,
                Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]),
                (Subscription.current_period_end.is_(None))
                | (Subscription.current_period_end > datetime.now(UTC)),
            )
        )
        return (subscription_count or 0) > 0

    async def _create_trial_message(
        self,
        *,
        user_id: UUID,
        cycle: TrialCycle,
        message_type: str,
        title: str,
        body: str,
    ) -> Alert | None:
        dedupe = stable_event_hash(
            {
                "trial_cycle_id": str(cycle.id),
                "message_type": message_type,
            }
        )
        existing = await self.session.scalar(select(Alert).where(Alert.deduplication_key == dedupe))
        if existing:
            from ai_market_monitor.services.notifications import NotificationDispatcher

            await NotificationDispatcher(self.session).enqueue_user_alert(existing)
            return existing
        cycle_starts_at = _as_aware(cycle.starts_at)
        cycle_ends_at = _as_aware(cycle.ends_at)
        alert = Alert(
            user_id=user_id,
            strategy_version_id=None,
            setup_instance_id=None,
            alert_type=AlertType.TRIAL,
            deduplication_key=dedupe,
            title=title,
            body=body,
            proof_receipt={
                "trial_id": str(cycle.trial_id),
                "trial_cycle_id": str(cycle.id),
                "trial_message_type": message_type,
                "trial_status": cycle.status,
                "cycle_number": cycle.cycle_number,
                "cycle_starts_at": cycle_starts_at.isoformat(),
                "cycle_ends_at": cycle_ends_at.isoformat(),
                "days_remaining": max(0, (cycle_ends_at - datetime.now(UTC)).days),
            },
            chart_snapshot_url=None,
            candle_timestamp=None,
        )
        self.session.add(alert)
        await self.session.flush()
        from ai_market_monitor.services.notifications import NotificationDispatcher

        await NotificationDispatcher(self.session).enqueue_user_alert(alert)
        return alert

    @staticmethod
    def _renewal_reason(cycle: TrialCycle) -> str:
        coverage = cycle.successful_scan_coverage or Decimal("0")
        if cycle.qualifying_alerts_generated > 0 and cycle.qualifying_alerts_delivered == 0:
            return "delivery_failure_service_credit"
        if coverage < Decimal("0.80000"):
            return "platform_service_credit"
        return "no_setup_matched"

    def _audit(
        self,
        user_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID | None,
        metadata: dict,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=user_id,
                actor_type="user" if action in {"trial.eligible", "trial.activated"} else "system",
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id else None,
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
