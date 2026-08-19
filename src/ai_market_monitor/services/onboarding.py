from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.security import (
    ContinuationTokenService,
    IdentityAssertionTokenService,
    InvalidContinuationToken,
    OnboardingAccessTokenService,
    token_digest,
)
from ai_market_monitor.db.models import (
    AttributionTouch,
    IdentityLinkToken,
    OnboardingSession,
    Trial,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import OnboardingStatus, OnboardingStep
from ai_market_monitor.schemas.onboarding import (
    DisclaimerRequest,
    GuidedSetupRequest,
    OnboardingSessionResponse,
    StartOnboardingRequest,
)
from ai_market_monitor.services.identity import IdentityService
from ai_market_monitor.services.risk_disclaimer import (
    record_acceptance as record_disclaimer_acceptance,
)
from ai_market_monitor.services.trials import TrialError, TrialLifecycleService


class OnboardingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class OnboardingService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings
        self.tokens = ContinuationTokenService(settings)
        self.access_tokens = OnboardingAccessTokenService(settings)
        self.identity_assertions = IdentityAssertionTokenService(settings)

    async def start(self, request: StartOnboardingRequest) -> OnboardingSessionResponse:
        identity_service = IdentityService(self.session, self.settings)
        trusted_assertion = False
        if request.identity_assertion:
            try:
                self.identity_assertions.verify(
                    request.identity_assertion,
                    provider=request.identity.provider.value,
                    provider_subject=request.identity.provider_subject,
                    email=str(request.identity.email) if request.identity.email else None,
                )
                trusted_assertion = True
            except InvalidContinuationToken as exc:
                raise OnboardingError("invalid_identity_assertion", str(exc)) from exc
        elif self.settings.is_production:
            raise OnboardingError(
                "identity_assertion_required",
                "Identity must be verified by Telegram or an email magic link",
            )
        user, identity, _ = await identity_service.resolve_or_create(
            request.identity, trusted_provider_assertion=trusted_assertion
        )
        onboarding = await self.session.scalar(
            select(OnboardingSession)
            .where(
                OnboardingSession.user_id == user.id,
                OnboardingSession.status.in_(
                    [OnboardingStatus.IN_PROGRESS, OnboardingStatus.BLOCKED]
                ),
            )
            .order_by(OnboardingSession.updated_at.desc())
        )
        if onboarding is None:
            onboarding = OnboardingSession(
                user_id=user.id,
                status=OnboardingStatus.IN_PROGRESS,
                current_step=OnboardingStep.DISCLAIMER,
                entry_channel=request.entry_channel,
                state_data={"identity_id": str(identity.id)},
            )
            self.session.add(onboarding)
            await self.session.flush()

        attribution = request.attribution
        self.session.add(
            AttributionTouch(
                user_id=user.id,
                onboarding_session_id=onboarding.id,
                source=attribution.source,
                medium=attribution.medium,
                campaign=attribution.campaign,
                referrer=attribution.referrer,
                referral_code=attribution.referral_code,
                entry_channel=request.entry_channel,
                landing_path=attribution.landing_path,
                consented=attribution.consented,
                metadata_json=attribution.metadata_json,
                created_at=datetime.now(UTC),
            )
        )
        token = await self._issue_link(user.id, onboarding.id)
        await self.session.commit()
        return self.response(
            onboarding,
            token=token,
            session_token=self.access_tokens.issue(user.id, onboarding.id),
        )

    async def resume(self, token: str) -> OnboardingSessionResponse:
        payload = self.tokens.decode(token)
        link = await self.session.scalar(
            select(IdentityLinkToken).where(
                IdentityLinkToken.token_digest == token_digest(token),
                IdentityLinkToken.consumed_at.is_(None),
                IdentityLinkToken.expires_at > datetime.now(UTC),
            )
        )
        if link is None:
            raise OnboardingError("invalid_link", "Continuation link is invalid, expired, or used")
        if str(link.user_id) != payload.get("user_id") or str(
            link.onboarding_session_id
        ) != payload.get("session_id"):
            raise OnboardingError("invalid_link", "Continuation link does not match the session")
        onboarding = await self.session.get(OnboardingSession, link.onboarding_session_id)
        if onboarding is None:
            raise OnboardingError("session_missing", "Onboarding session no longer exists")
        link.consumed_at = datetime.now(UTC)
        await self.session.commit()
        return self.response(
            onboarding,
            session_token=self.access_tokens.issue(onboarding.user_id, onboarding.id),
        )

    async def accept_disclaimer(
        self, onboarding: OnboardingSession, request: DisclaimerRequest
    ) -> OnboardingSession:
        self._require_step(onboarding, OnboardingStep.DISCLAIMER)
        if request.disclaimer_version != self.settings.disclaimer_version:
            raise OnboardingError(
                "disclaimer_outdated", "Review and accept the current disclaimer version"
            )
        identity = await self.session.get(UserIdentity, request.identity_id)
        if identity is None or identity.user_id != onboarding.user_id:
            raise OnboardingError("identity_missing", "Identity does not belong to this account")
        # Written through the one owner in `services/risk_disclaimer.py`. This used to
        # be its own copy of "check, then insert", and so did the Telegram bot — a legal
        # record with three writers is a record that can end up written three ways.
        # The identity has already been checked against this account above, so it is
        # passed rather than looked up again.
        await record_disclaimer_acceptance(
            self.session,
            user_id=onboarding.user_id,
            version=request.disclaimer_version,
            source=request.acceptance_source,
            identity_id=identity.id,
        )
        await self._activate_trial(onboarding.user_id)
        self._advance(onboarding, OnboardingStep.GUIDED_SETUP)
        await self.session.commit()
        return onboarding

    async def save_guided_setup(
        self, onboarding: OnboardingSession, request: GuidedSetupRequest
    ) -> OnboardingSession:
        self._require_step(onboarding, OnboardingStep.GUIDED_SETUP)
        state = dict(onboarding.state_data)
        state["guided_setup"] = request.model_dump(mode="json")
        onboarding.state_data = state
        self._advance(onboarding, OnboardingStep.INTERPRETATION)
        await self.session.commit()
        return onboarding

    async def mark_interpreted(
        self, onboarding: OnboardingSession, strategy_id: UUID, version_id: UUID, blocked: bool
    ) -> None:
        self._require_step(onboarding, OnboardingStep.INTERPRETATION)
        state = dict(onboarding.state_data)
        state.update({"strategy_id": str(strategy_id), "strategy_version_id": str(version_id)})
        onboarding.state_data = state
        if blocked:
            onboarding.status = OnboardingStatus.BLOCKED
            onboarding.blocked_reason = "Strategy interpretation requires clarification"
        else:
            onboarding.status = OnboardingStatus.IN_PROGRESS
            onboarding.blocked_reason = None
            self._advance(onboarding, OnboardingStep.APPROVAL)

    async def mark_approved(self, onboarding: OnboardingSession) -> None:
        self._require_step(onboarding, OnboardingStep.APPROVAL)
        self._advance(onboarding, OnboardingStep.VALIDATION)

    async def mark_previewed(self, onboarding: OnboardingSession, succeeded: bool) -> None:
        self._require_step(onboarding, OnboardingStep.VALIDATION)
        if succeeded:
            self._advance(onboarding, OnboardingStep.ACTIVATION)
            onboarding.status = OnboardingStatus.IN_PROGRESS
            onboarding.blocked_reason = None
        else:
            onboarding.status = OnboardingStatus.BLOCKED
            onboarding.blocked_reason = (
                "Recent market data preview failed; retry when data is available"
            )

    async def complete(self, onboarding: OnboardingSession) -> None:
        self._require_step(onboarding, OnboardingStep.ACTIVATION)
        now = datetime.now(UTC)
        onboarding.current_step = OnboardingStep.COMPLETE
        onboarding.status = OnboardingStatus.COMPLETED
        onboarding.completed_at = now
        onboarding.version += 1
        user = await self.session.get(User, onboarding.user_id)
        if user:
            user.onboarding_completed_at = now

    async def get_session(self, session_id: UUID, user_id: UUID) -> OnboardingSession:
        onboarding = await self.session.scalar(
            select(OnboardingSession)
            .options(selectinload(OnboardingSession.user))
            .where(OnboardingSession.id == session_id, OnboardingSession.user_id == user_id)
        )
        if onboarding is None:
            raise OnboardingError("session_missing", "Onboarding session not found")
        return onboarding

    async def _activate_trial(self, user_id: UUID) -> Trial:
        try:
            return await TrialLifecycleService(self.session, self.settings).activate(user_id)
        except TrialError as exc:
            raise OnboardingError(exc.code, str(exc)) from exc

    async def _issue_link(self, user_id: UUID, session_id: UUID) -> str:
        token, expires_at = self.tokens.issue(user_id, session_id)
        self.session.add(
            IdentityLinkToken(
                user_id=user_id,
                onboarding_session_id=session_id,
                token_digest=token_digest(token),
                expires_at=expires_at,
                created_at=datetime.now(UTC),
            )
        )
        return token

    @staticmethod
    def _require_step(onboarding: OnboardingSession, expected: OnboardingStep) -> None:
        if onboarding.current_step != expected:
            raise OnboardingError(
                "step_out_of_order",
                f"Complete {onboarding.current_step.value} before {expected.value}",
            )

    @staticmethod
    def _advance(onboarding: OnboardingSession, target: OnboardingStep) -> None:
        onboarding.current_step = target
        onboarding.status = OnboardingStatus.IN_PROGRESS
        onboarding.version += 1
        onboarding.last_error_code = None

    @staticmethod
    def response(
        onboarding: OnboardingSession,
        *,
        token: str | None = None,
        session_token: str | None = None,
    ) -> OnboardingSessionResponse:
        action = None
        if onboarding.status == OnboardingStatus.BLOCKED:
            action = onboarding.blocked_reason
        return OnboardingSessionResponse(
            session_id=onboarding.id,
            user_id=onboarding.user_id,
            status=onboarding.status,
            current_step=onboarding.current_step,
            state_data=onboarding.state_data,
            continuation_token=token,
            session_token=session_token,
            action_required=action,
        )
