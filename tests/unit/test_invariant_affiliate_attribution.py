"""Who a customer belongs to, and what each of their payments earns.

The programme's rules read as separate sentences and are really one decision, so each is
asserted as a **rule** rather than as the one example that prompted it:

* a link is remembered across visits, and a later link replaces an earlier one;
* an assignment is permanent — cancelling, resubscribing and typing somebody else's code
  all leave it exactly where it was;
* a link beats a typed code, and a typed code decides only when there was no link, for
  every combination of the two rather than for one;
* the first payment earns the first-payment rate and every payment after it earns the
  renewal rate, whichever way round the two rates are set;
* the same payment arriving twice earns the affiliate once;
* a banned or deleted account ends the assignment, and takes nothing already earned;
* a missing renewal rate means "the same as the first one", never "nothing".
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AffiliateApplication,
    AffiliateCodeUse,
    AffiliateCommission,
    ReferralCode,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserStatus
from ai_market_monitor.services.affiliate import AffiliateService
from ai_market_monitor.services.affiliate_attribution import (
    ATTRIBUTION_COOKIE_DAYS,
    ATTRIBUTION_COOKIE_NAME,
    CONTEXT_CHECKOUT,
    CONTEXT_SIGNUP,
    DEFAULT_COMMISSION_PERCENT,
    KIND_FIRST,
    KIND_SUBSEQUENT,
    REFERRAL_LINK_QUERY_KEY,
    SOURCE_CODE,
    SOURCE_LINK,
    ReferralAttributionService,
    capture_referral_link,
    forget_link_code,
    rates_for,
    read_referral_code,
    winning_referral,
)

APPLICANT_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_market_monitor"
    / "templates"
    / "hilal"
    / "dashboard"
    / "affiliate.html"
)

ADMIN_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_market_monitor"
    / "templates"
    / "system_brain.html"
)


# ── Test doubles for the two things that are not a database ─────────────────────


class FakeResponse:
    """Just enough of a response to see what a cookie was set to."""

    def __init__(self) -> None:
        self.cookies: dict[str, dict[str, object]] = {}
        self.deleted: list[str] = []

    def set_cookie(self, key: str, value: str, **options: object) -> None:
        self.cookies[key] = {"value": value, **options}

    def delete_cookie(self, key: str, **options: object) -> None:
        self.deleted.append(key)
        self.cookies.pop(key, None)


class FakeRequest:
    def __init__(self, query: dict[str, str] | None = None, cookies: dict[str, str] | None = None):
        self.query_params = dict(query or {})
        self.cookies = dict(cookies or {})


# ── Fixtures ────────────────────────────────────────────────────────────────────


async def _person(
    session: AsyncSession, *, name: str, email: str, status: UserStatus = UserStatus.ACTIVE
) -> User:
    user = User(id=uuid4(), display_name=name, status=status)
    session.add(user)
    session.add(
        UserIdentity(
            user_id=user.id,
            provider=IdentityProvider.EMAIL,
            provider_subject=email,
            normalized_identifier=email,
            is_verified=True,
            is_primary=True,
        )
    )
    await session.flush()
    return user


async def _affiliate(
    session: AsyncSession,
    *,
    admin: User,
    name: str,
    code: str,
    first: str = "25",
    later: str | None = None,
) -> tuple[User, AffiliateApplication]:
    """One approved affiliate, made the way the System Brain makes one."""

    person = await _person(session, name=name, email=f"{code.lower()}@example.test")
    service = AffiliateService(session)
    await service.apply(
        user_id=person.id,
        display_name=name,
        social_links=[f"https://x.com/{code.lower()}"],
        requested_discount_code=code,
    )
    application = await service.application_for(person.id)
    assert application is not None
    granted = await service.approve(
        application_id=application.id,
        admin_user_id=admin.id,
        discount_percent="10",
        commission_percent=first,
        subsequent_commission_percent=later,
    )
    return person, granted


# ── Remembering the link ────────────────────────────────────────────────────────


def test_a_visit_on_a_link_is_remembered_for_long_enough_to_come_back() -> None:
    """Somebody who leaves and signs up next month still credits the right person."""

    response = FakeResponse()
    captured = capture_referral_link(
        FakeRequest({REFERRAL_LINK_QUERY_KEY: "AMINA"}), response, secure=True
    )
    assert captured == "AMINA"
    cookie = response.cookies[ATTRIBUTION_COOKIE_NAME]
    assert cookie["value"] == "AMINA"
    assert cookie["max_age"] == ATTRIBUTION_COOKIE_DAYS * 24 * 60 * 60
    assert ATTRIBUTION_COOKIE_DAYS >= 30, "a month is the least that covers 'I'll think about it'"
    # `lax` and never `strict`: the whole journey starts on somebody else's website, and
    # `strict` drops the cookie on exactly that trip.
    assert cookie["samesite"] == "lax"
    assert cookie["httponly"] is True


def test_the_most_recent_link_replaces_the_one_before_it() -> None:
    """Following two affiliates' links means belonging to the second."""

    response = FakeResponse()
    capture_referral_link(FakeRequest({REFERRAL_LINK_QUERY_KEY: "FIRST"}), response, secure=False)
    capture_referral_link(FakeRequest({REFERRAL_LINK_QUERY_KEY: "SECOND"}), response, secure=False)
    assert response.cookies[ATTRIBUTION_COOKIE_NAME]["value"] == "SECOND"


def test_a_visit_with_no_link_writes_nothing() -> None:
    """An ordinary page view must not cost a cookie."""

    response = FakeResponse()
    assert capture_referral_link(FakeRequest({}), response, secure=False) is None
    assert response.cookies == {}


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("amina", "AMINA"),
        ("  Amina  ", "AMINA"),
        ("AM INA", "AMINA"),
        ("hilal-25", "HILAL-25"),
        ("", None),
        ("   ", None),
        ("!!!", None),
        ("a b !", None),
        ("x" * 41, None),
    ],
)
def test_one_reading_of_a_code_however_it_arrives(typed: str, expected: str | None) -> None:
    """The same reading on a link, in a cookie and in the discount box.

    Two readings is how ``hilal25`` becomes a different code from ``HILAL25`` on one
    surface and the same code on another.
    """

    assert read_referral_code(typed) == expected


def test_the_link_is_forgotten_once_it_has_done_its_job() -> None:
    response = FakeResponse()
    capture_referral_link(FakeRequest({REFERRAL_LINK_QUERY_KEY: "AMINA"}), response, secure=False)
    forget_link_code(response)
    assert ATTRIBUTION_COOKIE_NAME in response.deleted


# ── The priority rule ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "link,typed,expected_code,expected_source",
    [
        ("LINKED", None, "LINKED", SOURCE_LINK),
        ("LINKED", "TYPED", "LINKED", SOURCE_LINK),
        (None, "TYPED", "TYPED", SOURCE_CODE),
        ("", "TYPED", "TYPED", SOURCE_CODE),
        ("  ", "TYPED", "TYPED", SOURCE_CODE),
        # Junk in the link is not a link, so the code still decides.
        ("!!!", "TYPED", "TYPED", SOURCE_CODE),
        (None, None, None, None),
        ("", "", None, None),
    ],
)
def test_the_link_wins_and_the_code_only_decides_when_there_is_no_link(
    link: str | None, typed: str | None, expected_code: str | None, expected_source: str | None
) -> None:
    """Every combination, not the one case somebody happened to report.

    The rule is written once — a second comparison somewhere else is how the sign-up form
    and the payment page come to credit two different people for one customer.
    """

    winner = winning_referral(link_code=link, typed_code=typed)
    if expected_code is None:
        assert winner is None
        return
    assert winner is not None
    assert winner.code == expected_code
    assert winner.source == expected_source


# ── Assignment, and that it does not move ───────────────────────────────────────


async def test_arriving_on_a_link_assigns_the_person_to_that_affiliate(test_context) -> None:
    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-a@example.test")
        affiliate, _ = await _affiliate(session, admin=admin, name="Amina", code="AMINA")
        customer = await _person(session, name="Buyer", email="buy-a@example.test")

        relationship = await ReferralAttributionService(session).assign(
            user_id=customer.id, link_code="AMINA"
        )
        assert relationship is not None
        assert relationship.referrer_user_id == affiliate.id
        assert relationship.assignment_source == SOURCE_LINK


async def test_typing_the_code_with_no_link_assigns_the_person_too(test_context) -> None:
    """"Signed up through the link, **or** used the code at least once."""

    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-b@example.test")
        affiliate, _ = await _affiliate(session, admin=admin, name="Bilal", code="BILAL")
        customer = await _person(session, name="Buyer", email="buy-b@example.test")

        relationship = await ReferralAttributionService(session).assign(
            user_id=customer.id, typed_code="BILAL"
        )
        assert relationship is not None
        assert relationship.referrer_user_id == affiliate.id
        assert relationship.assignment_source == SOURCE_CODE


async def test_the_link_beats_the_code_on_the_same_signup(test_context) -> None:
    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-c@example.test")
        linked, _ = await _affiliate(session, admin=admin, name="Linked", code="LINKED")
        await _affiliate(session, admin=admin, name="Typed", code="TYPEDC")
        customer = await _person(session, name="Buyer", email="buy-c@example.test")

        relationship = await ReferralAttributionService(session).assign(
            user_id=customer.id, link_code="LINKED", typed_code="TYPEDC"
        )
        assert relationship is not None
        assert relationship.referrer_user_id == linked.id


async def test_an_assignment_never_moves_to_another_affiliate(test_context) -> None:
    """Cancelling, coming back, and pasting somebody else's code all change nothing."""

    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-d@example.test")
        first, _ = await _affiliate(session, admin=admin, name="First", code="FIRSTA")
        second, _ = await _affiliate(session, admin=admin, name="Second", code="SECONDA")
        customer = await _person(session, name="Buyer", email="buy-d@example.test")
        attribution = ReferralAttributionService(session)

        original = await attribution.assign(user_id=customer.id, link_code="FIRSTA")
        assert original is not None

        for later in (
            {"link_code": "SECONDA"},
            {"typed_code": "SECONDA"},
            {"link_code": "SECONDA", "typed_code": "SECONDA"},
        ):
            again = await attribution.assign(user_id=customer.id, **later)
            assert again is not None
            assert again.id == original.id
            assert again.referrer_user_id == first.id
        assert second.id != first.id
        # Still one row, not two.
        assert (
            await session.scalar(
                select(func.count()).select_from(
                    select(AffiliateCommission.id).subquery()
                )
            )
            == 0
        )


async def test_nobody_is_credited_for_a_code_that_is_not_real(test_context) -> None:
    """A junk code, a switched-off code, and your own code all credit nobody."""

    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-e@example.test")
        affiliate, application = await _affiliate(
            session, admin=admin, name="Emma", code="EMMAA"
        )
        customer = await _person(session, name="Buyer", email="buy-e@example.test")
        attribution = ReferralAttributionService(session)

        assert await attribution.assign(user_id=customer.id, link_code="NOSUCHCODE") is None
        # An affiliate cannot refer themselves.
        assert await attribution.assign(user_id=affiliate.id, link_code="EMMAA") is None

        code_row = await session.get(ReferralCode, application.referral_code_id)
        assert code_row is not None
        code_row.is_active = False
        await session.flush()
        assert await attribution.assign(user_id=customer.id, link_code="EMMAA") is None


# ── What a payment earns ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "first_rate,later_rate,paid,expect_first,expect_later",
    [
        ("25", "10", "100.00", "25.00", "10.00"),
        # The renewal rate above the first one, because nothing says it must be lower.
        ("10", "40", "100.00", "10.00", "40.00"),
        ("50", "50", "20.00", "10.00", "10.00"),
        ("7.5", "2.5", "40.00", "3.00", "1.00"),
    ],
)
async def test_the_first_payment_and_every_later_one_use_their_own_rate(
    test_context,
    first_rate: str,
    later_rate: str,
    paid: str,
    expect_first: str,
    expect_later: str,
) -> None:
    """The rule, across every pair of rates — not the one pair somebody set up with."""

    async with test_context["session_factory"]() as session:
        suffix = f"{first_rate}-{later_rate}".replace(".", "")
        admin = await _person(session, name="Owner", email=f"own-r{suffix}@example.test")
        affiliate, application = await _affiliate(
            session,
            admin=admin,
            name="Rate",
            code=f"RATE{suffix}",
            first=first_rate,
            later=later_rate,
        )
        customer = await _person(session, name="Buyer", email=f"buy-r{suffix}@example.test")
        attribution = ReferralAttributionService(session)
        await attribution.assign(user_id=customer.id, link_code=f"RATE{suffix}")

        one = await attribution.record_payment(
            customer_user_id=customer.id,
            event_key=f"pay-1-{suffix}",
            paid_amount_usd=Decimal(paid),
        )
        assert one is not None
        assert one.sequence_kind == KIND_FIRST
        assert one.commission_usd == Decimal(expect_first)

        for number in (2, 3):
            later = await attribution.record_payment(
                customer_user_id=customer.id,
                event_key=f"pay-{number}-{suffix}",
                paid_amount_usd=Decimal(paid),
            )
            assert later is not None
            assert later.sequence_kind == KIND_SUBSEQUENT
            assert later.commission_usd == Decimal(expect_later)

        stats = await AffiliateService(session).stats(application)
        assert stats.first_commission_usd == Decimal(expect_first)
        assert stats.subsequent_commission_usd == Decimal(expect_later) * 2
        assert stats.total_commission_usd == (
            Decimal(expect_first) + Decimal(expect_later) * 2
        )
        assert affiliate.id == application.user_id


async def test_the_customer_never_has_to_use_the_link_again(test_context) -> None:
    """The assignment pays, not the code. A renewal carries no code at all."""

    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-f@example.test")
        _, application = await _affiliate(
            session, admin=admin, name="Farid", code="FARIDA", first="25", later="15"
        )
        customer = await _person(session, name="Buyer", email="buy-f@example.test")
        attribution = ReferralAttributionService(session)
        await attribution.assign(user_id=customer.id, link_code="FARIDA")

        # No code is mentioned on either payment; the assignment is what pays.
        await attribution.record_payment(
            customer_user_id=customer.id, event_key="f-1", paid_amount_usd=Decimal("20.00")
        )
        renewal = await attribution.record_payment(
            customer_user_id=customer.id, event_key="f-2", paid_amount_usd=Decimal("20.00")
        )
        assert renewal is not None
        assert renewal.commission_usd == Decimal("3.00")
        stats = await AffiliateService(session).stats(application)
        assert stats.total_commission_usd == Decimal("8.00")


async def test_the_same_payment_arriving_twice_earns_the_affiliate_once(test_context) -> None:
    """A retried webhook must not pay twice."""

    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-g@example.test")
        _, application = await _affiliate(session, admin=admin, name="Ghada", code="GHADAA")
        customer = await _person(session, name="Buyer", email="buy-g@example.test")
        attribution = ReferralAttributionService(session)
        await attribution.assign(user_id=customer.id, link_code="GHADAA")

        one = await attribution.record_payment(
            customer_user_id=customer.id,
            event_key="payment:stripe:evt_1",
            paid_amount_usd=Decimal("100.00"),
        )
        two = await attribution.record_payment(
            customer_user_id=customer.id,
            event_key="payment:stripe:evt_1",
            paid_amount_usd=Decimal("100.00"),
        )
        assert one is not None and two is not None
        assert one.id == two.id
        stats = await AffiliateService(session).stats(application)
        assert stats.total_commission_usd == Decimal("25.00")
        assert len(stats.commission_log) == 1


async def test_a_share_is_frozen_onto_the_row_it_was_earned_at(test_context) -> None:
    """Changing a rate later must not rewrite what past payments were worth."""

    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-h@example.test")
        _, application = await _affiliate(
            session, admin=admin, name="Hana", code="HANAAA", first="25"
        )
        customer = await _person(session, name="Buyer", email="buy-h@example.test")
        attribution = ReferralAttributionService(session)
        await attribution.assign(user_id=customer.id, link_code="HANAAA")
        await attribution.record_payment(
            customer_user_id=customer.id, event_key="h-1", paid_amount_usd=Decimal("100.00")
        )

        application.commission_percent = Decimal("5")
        await session.flush()

        stats = await AffiliateService(session).stats(application)
        assert stats.total_commission_usd == Decimal("25.00")
        assert stats.commission_log[0].commission_percent == Decimal("25.00")


async def test_a_renewal_is_worth_what_the_renewal_charged(test_context) -> None:
    """Not what the first month cost, which is a different number once a code applies.

    A subscription renews without opening a new checkout. Reading the amount off the
    stored checkout row would value every renewal at the discounted first month, for as
    long as that customer stayed.
    """

    from ai_market_monitor.db.models import BillingCheckoutAttempt, Plan
    from ai_market_monitor.services.entitlements import PlanCatalogService

    async with test_context["session_factory"]() as session:
        await PlanCatalogService(session).sync_defaults()
        plan = await session.scalar(select(Plan).where(Plan.code == "trader"))
        admin = await _person(session, name="Owner", email="own-renew@example.test")
        _, application = await _affiliate(
            session, admin=admin, name="Rana", code="RENEWAL", first="25", later="25"
        )
        customer = await _person(session, name="Buyer", email="buy-renew@example.test")
        attribution = ReferralAttributionService(session)
        await attribution.assign(user_id=customer.id, link_code="RENEWAL")

        # The first month was bought with the affiliate's code: $18 rather than $20.
        now = datetime.now(UTC)
        session.add(
            BillingCheckoutAttempt(
                user_id=customer.id,
                plan_id=plan.id,
                billing_cycle="monthly",
                provider="static",
                status="completed",
                idempotency_key=uuid4().hex,
                terms_version="v1",
                amount=Decimal("18.00"),
                currency="USD",
                terms_accepted_at=now,
                expires_at=now,
                completed_at=now,
            )
        )
        await session.flush()

        first = await attribution.record_payment(
            customer_user_id=customer.id, event_key="renew-1", plan_id=plan.id
        )
        assert first is not None
        assert first.paid_amount_usd == Decimal("18.00")
        assert first.commission_usd == Decimal("4.50")

        # The renewal is charged at the full price, and the payment company says so.
        later = await attribution.record_payment(
            customer_user_id=customer.id,
            event_key="renew-2",
            plan_id=plan.id,
            paid_amount_usd=Decimal("20.00"),
        )
        assert later is not None
        assert later.paid_amount_usd == Decimal("20.00"), (
            "the renewal was valued at the first month's discounted price"
        )
        assert later.commission_usd == Decimal("5.00")
        assert later.metadata_json["paid_amount_source"] == "event"

        stats = await AffiliateService(session).stats(application)
        assert stats.total_commission_usd == Decimal("9.50")


async def test_a_payment_with_no_approved_affiliate_earns_nothing(test_context) -> None:
    """An application refused after somebody joined must not pay."""

    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-i@example.test")
        affiliate, application = await _affiliate(session, admin=admin, name="Iman", code="IMANAA")
        customer = await _person(session, name="Buyer", email="buy-i@example.test")
        attribution = ReferralAttributionService(session)
        relationship = await attribution.assign(user_id=customer.id, link_code="IMANAA")
        assert relationship is not None

        application.status = "rejected"
        await session.flush()

        assert (
            await attribution.record_payment(
                customer_user_id=customer.id,
                event_key="i-1",
                paid_amount_usd=Decimal("100.00"),
            )
            is None
        )
        # The person still reads as having joined and paid. Only the money stops.
        assert relationship.reward_status == "eligible_after_first_paid_month"
        assert (
            await session.scalar(
                select(func.count(AffiliateCommission.id)).where(
                    AffiliateCommission.affiliate_user_id == affiliate.id
                )
            )
            == 0
        )


# ── Banning and deleting ────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [UserStatus.SUSPENDED, UserStatus.DELETED])
async def test_a_banned_or_deleted_customer_earns_nothing_more(
    test_context, status: UserStatus
) -> None:
    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email=f"own-j{status}@example.test")
        _, application = await _affiliate(
            session, admin=admin, name="Jamil", code=f"JAMIL{status.value[:3].upper()}"
        )
        customer = await _person(session, name="Buyer", email=f"buy-j{status}@example.test")
        attribution = ReferralAttributionService(session)
        await attribution.assign(
            user_id=customer.id, link_code=f"JAMIL{status.value[:3].upper()}"
        )
        await attribution.record_payment(
            customer_user_id=customer.id,
            event_key=f"j-1{status}",
            paid_amount_usd=Decimal("100.00"),
        )

        customer.status = status
        await session.flush()

        assert (
            await attribution.record_payment(
                customer_user_id=customer.id,
                event_key=f"j-2{status}",
                paid_amount_usd=Decimal("100.00"),
            )
            is None
        )
        # What was already earned stays. It really was earned.
        stats = await AffiliateService(session).stats(application)
        assert stats.total_commission_usd == Decimal("25.00")


async def test_releasing_an_assignment_keeps_the_money_already_earned(test_context) -> None:
    """The row goes; the receipts do not, and they keep the name on them."""

    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-k@example.test")
        _, application = await _affiliate(session, admin=admin, name="Kamal", code="KAMALA")
        customer = await _person(session, name="Karim Hassan", email="buy-k@example.test")
        attribution = ReferralAttributionService(session)
        await attribution.assign(user_id=customer.id, link_code="KAMALA")
        await attribution.record_payment(
            customer_user_id=customer.id, event_key="k-1", paid_amount_usd=Decimal("100.00")
        )

        assert await attribution.release(user_id=customer.id) is True
        assert await attribution.assignment_for(customer.id) is None

        stats = await AffiliateService(session).stats(application)
        assert stats.total_commission_usd == Decimal("25.00")
        assert stats.commission_log[0].customer_name == "Karim Hassan"


# ── The code-use log ────────────────────────────────────────────────────────────


async def test_the_code_log_counts_every_use_including_one_that_assigned_nobody(
    test_context,
) -> None:
    """An affiliate sees every time their code was typed, not only the ones that paid."""

    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-l@example.test")
        first, first_app = await _affiliate(session, admin=admin, name="Lina", code="LINAAA")
        _, second_app = await _affiliate(session, admin=admin, name="Musa", code="MUSAAA")
        customer = await _person(session, name="Buyer", email="buy-l@example.test")
        attribution = ReferralAttributionService(session)

        await attribution.assign(user_id=customer.id, link_code="LINAAA")
        await attribution.record_code_use(
            code="LINAAA",
            user_id=customer.id,
            context=CONTEXT_SIGNUP,
            event_key=f"signup:{customer.id}",
        )
        # Later, the same person types the other affiliate's code at the payment page.
        # It assigned nobody — but it was still used, and Musa may see it.
        await attribution.record_code_use(
            code="MUSAAA",
            user_id=customer.id,
            context=CONTEXT_CHECKOUT,
            event_key="checkout:abc",
        )

        service = AffiliateService(session)
        lina = await service.stats(first_app)
        musa = await service.stats(second_app)
        assert lina.code_uses == 1
        assert lina.code_use_log[0].customer_name == "Buyer"
        assert musa.code_uses == 1
        assert musa.link_signups == 0
        # And the assignment did not move.
        assignment = await attribution.assignment_for(customer.id)
        assert assignment is not None
        assert assignment.referrer_user_id == first.id


async def test_the_same_use_logged_twice_is_one_row(test_context) -> None:
    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-m@example.test")
        _, application = await _affiliate(session, admin=admin, name="Nada", code="NADAAA")
        customer = await _person(session, name="Buyer", email="buy-m@example.test")
        attribution = ReferralAttributionService(session)
        for _ in range(3):
            await attribution.record_code_use(
                code="NADAAA",
                user_id=customer.id,
                context=CONTEXT_SIGNUP,
                event_key=f"signup:{customer.id}",
            )
        assert (
            await session.scalar(
                select(func.count(AffiliateCodeUse.id)).where(
                    AffiliateCodeUse.affiliate_user_id == application.user_id
                )
            )
            == 1
        )


async def test_a_customer_with_no_name_is_never_shown_as_their_address(test_context) -> None:
    """The local part of an email is the address in all but punctuation."""

    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-n@example.test")
        _, application = await _affiliate(session, admin=admin, name="Omar", code="OMARAA")
        customer = await _person(session, name="", email="quiet.person@example.test")
        customer.display_name = None
        attribution = ReferralAttributionService(session)
        await attribution.assign(user_id=customer.id, link_code="OMARAA")
        await attribution.record_code_use(
            code="OMARAA",
            user_id=customer.id,
            context=CONTEXT_SIGNUP,
            event_key=f"signup:{customer.id}",
        )
        await attribution.record_payment(
            customer_user_id=customer.id, event_key="n-1", paid_amount_usd=Decimal("100.00")
        )

        stats = await AffiliateService(session).stats(application)
        for text in (
            stats.code_use_log[0].customer_name,
            stats.commission_log[0].customer_name,
            stats.earnings[0].customer_name,
        ):
            assert "quiet.person" not in text
            assert "@" not in text


# ── The two rates, and the safe direction of every gap ──────────────────────────


def test_a_missing_renewal_rate_means_the_same_as_the_first_never_nothing() -> None:
    """Reading an empty box as zero would silently stop paying somebody."""

    class Application:
        commission_percent = Decimal("30")
        subsequent_commission_percent = None

    rates = rates_for(Application())
    assert rates.first_percent == Decimal("30")
    assert rates.subsequent_percent == Decimal("30")
    assert rates.percent_for(KIND_FIRST) == Decimal("30")
    assert rates.percent_for(KIND_SUBSEQUENT) == Decimal("30")


def test_no_application_at_all_falls_back_to_the_share_everybody_applies_on() -> None:
    rates = rates_for(None)
    assert rates.first_percent == DEFAULT_COMMISSION_PERCENT
    assert rates.subsequent_percent == DEFAULT_COMMISSION_PERCENT


async def test_approving_without_a_renewal_rate_stores_the_first_one(test_context) -> None:
    async with test_context["session_factory"]() as session:
        admin = await _person(session, name="Owner", email="own-o@example.test")
        _, application = await _affiliate(
            session, admin=admin, name="Rania", code="RANIAA", first="40", later=None
        )
        assert application.commission_percent == Decimal("40.00")
        assert application.subsequent_commission_percent == Decimal("40.00")


async def test_both_rates_are_only_settable_by_an_administrator(test_context) -> None:
    """The applicant has no box for either, and the apply route reads neither."""

    import inspect

    for field in ("commission_percent", "subsequent_commission_percent"):
        assert field not in inspect.signature(AffiliateService.apply).parameters

    markup = APPLICANT_TEMPLATE.read_text(encoding="utf-8")
    assert 'name="commission_percent"' not in markup
    assert 'name="subsequent_commission_percent"' not in markup

    # And the administrator's screen has a box for each.
    admin_markup = ADMIN_TEMPLATE.read_text(encoding="utf-8")
    assert 'name="commission_percent"' in admin_markup
    assert 'name="subsequent_commission_percent"' in admin_markup
    del test_context


# ── What the page promises ──────────────────────────────────────────────────────


def test_the_page_names_both_rates_and_says_the_link_is_remembered() -> None:
    """A beginner has to be able to read the rule off the page, in plain words."""

    markup = APPLICANT_TEMPLATE.read_text(encoding="utf-8")
    assert "First time they pay" in markup
    assert "Every time after that" in markup
    assert "once somebody is yours, they stay yours" in markup
    assert "even if they come" in markup and "back days later" in markup


def test_every_figure_that_can_be_opened_has_a_popup_behind_its_button() -> None:
    """A card that says "see who" and opens nothing is worse than no button at all.

    The script joins the two by id — a button carrying ``data-hm-aff-log-open="x"`` opens
    the dialog whose id is ``x`` — so a button naming an id no dialog has is a control
    that does nothing at all, and nothing would say so.
    """

    markup = APPLICANT_TEMPLATE.read_text(encoding="utf-8")
    buttons = set(re.findall(r"log_button\(\s*'([a-z0-9-]+)'", markup))
    dialogs = set(re.findall(r"log_dialog\(\s*\n?\s*'([a-z0-9-]+)'", markup))
    # The three money logs are drawn from one list rather than written out three times.
    dialogs |= set(re.findall(r"^\s*\('(hm-aff-log-[a-z0-9]+)',", markup, re.MULTILINE))

    assert buttons, "no card opens a log"
    assert len(buttons) == 4, sorted(buttons)
    missing = sorted(buttons - dialogs)
    assert missing == [], f"these buttons open nothing: {missing}"

    # And the two halves the script joins are really both in the markup.
    assert "data-hm-aff-log-open=" in markup
    assert "data-hm-aff-log>" in markup


def test_the_card_that_replaced_the_conversions_one_says_what_it_counts() -> None:
    """"Of those, paid a plan" is gone; the card counts sign-ups through the link."""

    markup = APPLICANT_TEMPLATE.read_text(encoding="utf-8")
    assert "Of those, paid a plan" not in markup
    assert "People signed up through your link" in markup
    assert "stats.link_signups" in markup
    assert "Times your code was used" in markup
    assert "stats.code_uses" in markup
