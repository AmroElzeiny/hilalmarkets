"""The affiliate programme, from the form somebody fills to the money going out.

The rules asserted here are the ones that cost real money if they break, so each is
asserted as a rule and not as one example:

* an applicant asks; only an administrator sets the code, the discount and the share;
* a refusal reopens the form, and a second application is a fresh request, not a refusal
  with new text on it;
* a balance is what was earned minus what has already been asked for, so pressing the
  payout button twice cannot pay twice;
* a refused payout puts the money back;
* every coin and network offered costs at most the fee cap to send, and a pair that is
  not offered is refused rather than accepted quietly;
* an affiliate is shown who joined and never their email address.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AffiliateApplication,
    ReferralCode,
    ReferralRelationship,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider
from ai_market_monitor.services.affiliate import (
    DEFAULT_COMMISSION_PERCENT,
    AffiliateError,
    AffiliateService,
    normalize_discount_code,
    normalize_social_links,
)
from ai_market_monitor.services.affiliate_payout_options import (
    ALTERNATIVE_METHOD_EMAIL,
    MAXIMUM_NETWORK_FEE_USD,
    MINIMUM_PAYOUT_USD,
    PAYOUT_CURRENCIES,
    network_for,
    payout_options_payload,
)

#: The applicant's page. Read as text because the rule being asserted is about what the
#: form *offers*, and a control that is not in the markup cannot be sent by a browser.
APPLICANT_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_market_monitor"
    / "templates"
    / "hilal"
    / "dashboard"
    / "affiliate.html"
)


async def _person(session: AsyncSession, *, name: str, email: str) -> User:
    user = User(id=uuid4(), display_name=name)
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


async def _approved(
    session: AsyncSession,
    *,
    user: User,
    admin: User,
    commission: str = "25",
) -> AffiliateApplication:
    service = AffiliateService(session)
    await service.apply(
        user_id=user.id,
        display_name=user.display_name or "Somebody",
        social_links=["https://x.com/someone"],
        requested_discount_code="someone",
    )
    application = await service.approve(
        application_id=(await service.application_for(user.id)).id,
        admin_user_id=admin.id,
        discount_percent="10",
        commission_percent=commission,
    )
    await session.flush()
    return application


async def _converted_referral(
    session: AsyncSession,
    *,
    affiliate: User,
    customer: User,
    paid_usd: str,
) -> ReferralRelationship:
    """A referral that has become money, written the way the referral service writes it."""

    relationship = ReferralRelationship(
        referrer_user_id=affiliate.id,
        referred_user_id=customer.id,
        status="paid_converted",
        reward_status="eligible_after_first_paid_month",
        metadata_json={"paid_amount_usd": paid_usd},
    )
    session.add(relationship)
    await session.flush()
    return relationship


# ── The catalogue and its rule ───────────────────────────────────────────────────


def test_every_payout_option_is_cheap_enough_to_send() -> None:
    """The rule is the fee cap; the list is only what currently satisfies it.

    A payout of five dollars losing two of them to a withdrawal fee is not a payout. This
    fails the moment a coin or a network is added that breaks the cap, which is the whole
    reason the fees are written down rather than assumed.
    """

    for currency in PAYOUT_CURRENCIES:
        assert currency.networks, f"{currency.key} is offered with nowhere to send it"
        for network in currency.networks:
            assert network.typical_fee_usd <= MAXIMUM_NETWORK_FEE_USD, (
                f"{currency.key} on {network.label} costs about "
                f"${network.typical_fee_usd} to send, over the ${MAXIMUM_NETWORK_FEE_USD} cap"
            )


def test_the_four_named_coins_are_all_offered() -> None:
    """USDT, USDC, BNB and LTC are the ones people asked for by name."""

    offered = {currency.key for currency in PAYOUT_CURRENCIES}
    assert {"USDT", "USDC", "BNB", "LTC"}.issubset(offered)
    # And three more, so somebody whose wallet holds none of the four still has a way.
    assert len(offered) >= 7


def test_a_coin_and_network_pair_that_is_not_offered_is_refused() -> None:
    """USDC does not run on Litecoin. Accepting the pair would send money nowhere."""

    assert network_for("USDC", "litecoin") is None
    assert network_for("LTC", "bep20") is None
    assert network_for("nonsense", "bep20") is None
    assert network_for("USDT", "trc20") is not None


def test_the_payload_the_page_draws_carries_the_fee() -> None:
    """A person choosing between two networks is choosing between two amounts arriving."""

    for currency in payout_options_payload():
        assert currency["plain_words"]
        for network in currency["networks"]:
            assert network["typical_fee_usd"]
            assert network["address_hint"]


# ── What an applicant may write ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("someone", "SOMEONE"),
        ("  Some-One  ", "SOME-ONE"),
        ("some one", "SOMEONE"),
        ("halal_2026", "HALAL_2026"),
    ],
)
def test_a_code_has_one_spelling(raw: str, expected: str) -> None:
    """Two people cannot claim the same code in two different cases."""

    assert normalize_discount_code(raw) == expected


@pytest.mark.parametrize("raw", ["", "ab", "!!!", "x" * 41, "-starts-with-dash"])
def test_a_code_that_cannot_be_read_out_is_refused(raw: str) -> None:
    with pytest.raises(AffiliateError) as error:
        normalize_discount_code(raw)
    assert error.value.code == "invalid_code"


def test_a_link_without_a_scheme_is_completed_not_refused() -> None:
    """Somebody typing ``instagram.com/name`` has given a usable address."""

    assert normalize_social_links(["instagram.com/name"]) == ["https://instagram.com/name"]


def test_something_that_is_not_a_link_is_refused_rather_than_stored() -> None:
    with pytest.raises(AffiliateError) as error:
        normalize_social_links(["my instagram"])
    assert error.value.code == "invalid_link"


def test_at_least_one_place_to_share_is_required() -> None:
    with pytest.raises(AffiliateError) as error:
        normalize_social_links(["", "   "])
    assert error.value.code == "links_missing"


# ── Applying, and being answered ─────────────────────────────────────────────────


async def test_an_applicant_cannot_set_their_own_money(test_context) -> None:
    """There is no share for an applicant to choose. Approval sets the real one."""

    async with test_context["session_factory"]() as session:
        user = await _person(session, name="Amina", email="amina@example.test")
        admin = await _person(session, name="Owner", email="owner@example.test")
        service = AffiliateService(session)
        await service.apply(
            user_id=user.id,
            display_name="Amina",
            social_links=["https://x.com/amina"],
            requested_discount_code="amina",
        )
        application = await service.application_for(user.id)
        # Everybody applies on the standard share, and it is stored so the row says which
        # share the application was made under even if the standard one changes later.
        assert application.requested_commission_percent == DEFAULT_COMMISSION_PERCENT
        # Nothing about the granted programme exists yet.
        assert application.commission_percent is None
        assert application.discount_code is None
        assert application.discount_percent is None

        granted = await service.approve(
            application_id=application.id,
            admin_user_id=admin.id,
            discount_percent="10",
            # The administrator left the share box alone.
            commission_percent=None,
        )
        assert granted.commission_percent == DEFAULT_COMMISSION_PERCENT
        assert granted.discount_percent == Decimal("10.00")
        assert granted.discount_code == "AMINA"


def test_the_form_offers_no_way_to_choose_a_share() -> None:
    """Not a disabled box — no box.

    A read-only or disabled input still sits in the form, still sends its value in the
    POST body, and still reads to a screen reader as a control that will not work. The
    only version of "fixed" that a crafted request cannot argue with is a share that is
    not a form field and not a parameter.
    """

    markup = APPLICANT_TEMPLATE.read_text(encoding="utf-8")
    assert 'name="requested_commission_percent"' not in markup
    assert 'id="affiliate-share"' not in markup
    assert "The share you would like" not in markup

    # And nothing on the applicant's side of the service will accept one either.
    assert "requested_commission_percent" not in inspect.signature(
        AffiliateService.apply
    ).parameters


def test_the_form_states_the_share_and_who_to_ask_for_more() -> None:
    """A number nobody can change has to be visible, and the way to ask has to be given.

    Both come from the same constants the service uses, so the page cannot promise a
    share the service does not store or an address nobody reads.
    """

    markup = APPLICANT_TEMPLATE.read_text(encoding="utf-8")
    assert "hm-aff-share" in markup
    assert "{{ default_commission }}%" in markup
    assert "mailto:{{ alternative_method_email }}" in markup
    assert ALTERNATIVE_METHOD_EMAIL == "office@hilalmarkets.com"

    share_block = markup.split('class="hm-aff-share"', 1)[1].split("</div>\n\n", 1)[0]
    assert "alternative_method_email" in share_block, (
        "the way to ask for more has to sit with the share itself, not elsewhere"
    )


@pytest.mark.parametrize("granted", ["10", "25", "40", "60"])
async def test_only_the_administrator_can_move_the_share(test_context, granted) -> None:
    """Fixed for the applicant, still free for whoever decides. Every value, not one."""

    async with test_context["session_factory"]() as session:
        user = await _person(
            session, name="Nadia", email=f"nadia{granted}@example.test"
        )
        admin = await _person(session, name="Owner", email=f"owner{granted}@example.test")
        service = AffiliateService(session)
        await service.apply(
            user_id=user.id,
            display_name="Nadia",
            social_links=["https://x.com/nadia"],
            requested_discount_code=f"nadia{granted}",
        )
        application = await service.application_for(user.id)
        assert application.requested_commission_percent == DEFAULT_COMMISSION_PERCENT

        decided = await service.approve(
            application_id=application.id,
            admin_user_id=admin.id,
            discount_percent="10",
            commission_percent=granted,
        )
        assert decided.commission_percent == Decimal(granted).quantize(Decimal("0.01"))
        # The request is left alone. It records what was applied for, not what was given.
        assert decided.requested_commission_percent == DEFAULT_COMMISSION_PERCENT


async def test_approving_without_a_discount_is_refused(test_context) -> None:
    """A discount nobody chose is a price change nobody approved."""

    async with test_context["session_factory"]() as session:
        user = await _person(session, name="Bilal", email="bilal@example.test")
        admin = await _person(session, name="Owner", email="owner2@example.test")
        service = AffiliateService(session)
        await service.apply(
            user_id=user.id,
            display_name="Bilal",
            social_links=["https://x.com/bilal"],
            requested_discount_code="bilal",
        )
        application = await service.application_for(user.id)
        with pytest.raises(AffiliateError) as error:
            await service.approve(
                application_id=application.id,
                admin_user_id=admin.id,
                discount_percent="",
            )
        assert error.value.code == "discount_missing"


async def test_approval_creates_the_code_a_customer_types(test_context) -> None:
    async with test_context["session_factory"]() as session:
        user = await _person(session, name="Yusuf", email="yusuf@example.test")
        admin = await _person(session, name="Owner", email="owner3@example.test")
        application = await _approved(session, user=user, admin=admin)
        code = await session.get(ReferralCode, application.referral_code_id)
        assert code is not None
        assert code.code == "SOMEONE"
        assert code.owner_user_id == user.id
        assert code.is_active is True


async def test_a_code_somebody_else_owns_is_refused(test_context) -> None:
    """Two affiliates sharing a code means neither can be paid correctly."""

    async with test_context["session_factory"]() as session:
        first = await _person(session, name="First", email="first@example.test")
        second = await _person(session, name="Second", email="second@example.test")
        admin = await _person(session, name="Owner", email="owner4@example.test")
        await _approved(session, user=first, admin=admin)

        service = AffiliateService(session)
        await service.apply(
            user_id=second.id,
            display_name="Second",
            social_links=["https://x.com/second"],
            requested_discount_code="someone",
        )
        with pytest.raises(AffiliateError) as error:
            await service.approve(
                application_id=(await service.application_for(second.id)).id,
                admin_user_id=admin.id,
                discount_percent="10",
            )
        assert error.value.code == "code_taken"


async def test_a_refusal_reopens_the_form_and_the_next_try_is_a_fresh_request(
    test_context,
) -> None:
    """A second application must not read as a refusal with new text stapled to it."""

    async with test_context["session_factory"]() as session:
        user = await _person(session, name="Hana", email="hana@example.test")
        admin = await _person(session, name="Owner", email="owner5@example.test")
        service = AffiliateService(session)
        await service.apply(
            user_id=user.id,
            display_name="Hana",
            social_links=["https://x.com/hana"],
            requested_discount_code="hana",
        )
        rejected = await service.reject(
            application_id=(await service.application_for(user.id)).id,
            admin_user_id=admin.id,
            decision_note="Tell us more about your audience.",
        )
        assert rejected.status == "rejected"
        assert rejected.decision_note

        again = await service.apply(
            user_id=user.id,
            display_name="Hana Ali",
            social_links=["https://youtube.com/@hana"],
            requested_discount_code="hanaali",
        )
        assert again.status == "pending"
        assert again.decision_note is None
        assert again.decided_at is None
        assert again.display_name == "Hana Ali"
        # Still one application, not two.
        assert (await service.application_for(user.id)).id == rejected.id


async def test_applying_twice_while_waiting_is_refused(test_context) -> None:
    async with test_context["session_factory"]() as session:
        user = await _person(session, name="Omar", email="omar@example.test")
        service = AffiliateService(session)
        await service.apply(
            user_id=user.id,
            display_name="Omar",
            social_links=["https://x.com/omar"],
            requested_discount_code="omar",
        )
        with pytest.raises(AffiliateError) as error:
            await service.apply(
                user_id=user.id,
                display_name="Omar",
                social_links=["https://x.com/omar"],
                requested_discount_code="omar2",
            )
        assert error.value.code == "already_pending"


async def test_an_application_cannot_be_decided_twice(test_context) -> None:
    async with test_context["session_factory"]() as session:
        user = await _person(session, name="Sara", email="sara@example.test")
        admin = await _person(session, name="Owner", email="owner6@example.test")
        application = await _approved(session, user=user, admin=admin)
        service = AffiliateService(session)
        with pytest.raises(AffiliateError) as error:
            await service.reject(
                application_id=application.id,
                admin_user_id=admin.id,
            )
        assert error.value.code == "already_decided"


# ── The money ────────────────────────────────────────────────────────────────────


async def test_commission_is_a_share_of_what_the_customer_actually_paid(
    test_context,
) -> None:
    """Never of an assumed plan price, which would show money nobody received."""

    async with test_context["session_factory"]() as session:
        affiliate = await _person(session, name="Zaid", email="zaid@example.test")
        admin = await _person(session, name="Owner", email="owner7@example.test")
        customer = await _person(session, name="Layla", email="layla@example.test")
        application = await _approved(session, user=affiliate, admin=admin, commission="25")
        await _converted_referral(
            session, affiliate=affiliate, customer=customer, paid_usd="40.00"
        )

        stats = await AffiliateService(session).stats(application)
        assert stats.uses == 1
        assert stats.paid_conversions == 1
        assert stats.total_commission_usd == Decimal("10.00")
        assert stats.available_usd == Decimal("10.00")


async def test_a_referral_with_no_recorded_payment_earns_nothing(test_context) -> None:
    """The honest answer is zero, not a guess at what the plan probably costs."""

    async with test_context["session_factory"]() as session:
        affiliate = await _person(session, name="Nur", email="nur@example.test")
        admin = await _person(session, name="Owner", email="owner8@example.test")
        customer = await _person(session, name="Ali", email="ali@example.test")
        application = await _approved(session, user=affiliate, admin=admin)
        relationship = ReferralRelationship(
            referrer_user_id=affiliate.id,
            referred_user_id=customer.id,
            status="paid_converted",
            reward_status="eligible_after_first_paid_month",
            metadata_json={},
        )
        session.add(relationship)
        await session.flush()

        stats = await AffiliateService(session).stats(application)
        assert stats.total_commission_usd == Decimal("0.00")
        assert stats.can_request_payout is False


async def test_an_affiliate_sees_names_and_dates_and_never_an_email(test_context) -> None:
    """A customer's address is not the affiliate's to be given."""

    async with test_context["session_factory"]() as session:
        affiliate = await _person(session, name="Iman", email="iman@example.test")
        admin = await _person(session, name="Owner", email="owner9@example.test")
        customer = await _person(session, name="Karim Hassan", email="karim@example.test")
        application = await _approved(session, user=affiliate, admin=admin)
        await _converted_referral(
            session, affiliate=affiliate, customer=customer, paid_usd="20.00"
        )

        stats = await AffiliateService(session).stats(application)
        earning = stats.earnings[0]
        assert earning.customer_name == "Karim Hassan"
        assert isinstance(earning.joined_at, datetime)
        rendered = f"{earning.customer_name}{earning.commission_usd}"
        assert "karim@example.test" not in rendered
        assert "@" not in earning.customer_name


async def test_a_customer_with_no_name_is_not_identified_by_their_address(
    test_context,
) -> None:
    """The local part of an email is the address in all but punctuation."""

    async with test_context["session_factory"]() as session:
        affiliate = await _person(session, name="Rania", email="rania@example.test")
        admin = await _person(session, name="Owner", email="owner10@example.test")
        customer = await _person(session, name="", email="quiet.person@example.test")
        customer.display_name = None
        application = await _approved(session, user=affiliate, admin=admin)
        await _converted_referral(
            session, affiliate=affiliate, customer=customer, paid_usd="20.00"
        )

        stats = await AffiliateService(session).stats(application)
        assert "quiet.person" not in stats.earnings[0].customer_name


# ── Payouts ──────────────────────────────────────────────────────────────────────


async def test_a_payout_below_the_minimum_is_refused(test_context) -> None:
    async with test_context["session_factory"]() as session:
        affiliate = await _person(session, name="Tariq", email="tariq@example.test")
        admin = await _person(session, name="Owner", email="owner11@example.test")
        customer = await _person(session, name="Buyer", email="buyer1@example.test")
        application = await _approved(session, user=affiliate, admin=admin)
        # 25% of $10 is $2.50, under the $5 floor.
        await _converted_referral(
            session, affiliate=affiliate, customer=customer, paid_usd="10.00"
        )
        with pytest.raises(AffiliateError) as error:
            await AffiliateService(session).request_payout(
                application=application,
                currency="USDT",
                network="bep20",
                destination_address="0x00112233445566778899aabbccddeeff00112233",
            )
        assert error.value.code == "below_minimum"


async def test_asking_twice_cannot_pay_twice(test_context) -> None:
    """The second request sees a balance the first one has already claimed."""

    async with test_context["session_factory"]() as session:
        affiliate = await _person(session, name="Hamza", email="hamza@example.test")
        admin = await _person(session, name="Owner", email="owner12@example.test")
        customer = await _person(session, name="Buyer", email="buyer2@example.test")
        application = await _approved(session, user=affiliate, admin=admin)
        await _converted_referral(
            session, affiliate=affiliate, customer=customer, paid_usd="100.00"
        )
        service = AffiliateService(session)

        first = await service.request_payout(
            application=application,
            currency="USDT",
            network="bep20",
            destination_address="0x00112233445566778899aabbccddeeff00112233",
        )
        assert first.amount_usd == Decimal("25.00")

        stats = await service.stats(application)
        assert stats.requested_or_paid_usd == Decimal("25.00")
        assert stats.available_usd == Decimal("0.00")

        with pytest.raises(AffiliateError) as error:
            await service.request_payout(
                application=application,
                currency="USDT",
                network="bep20",
                destination_address="0x00112233445566778899aabbccddeeff00112233",
            )
        assert error.value.code == "below_minimum"


async def test_a_refused_payout_returns_the_money(test_context) -> None:
    """It was never sent, so it is still theirs."""

    async with test_context["session_factory"]() as session:
        affiliate = await _person(session, name="Dana", email="dana@example.test")
        admin = await _person(session, name="Owner", email="owner13@example.test")
        customer = await _person(session, name="Buyer", email="buyer3@example.test")
        application = await _approved(session, user=affiliate, admin=admin)
        await _converted_referral(
            session, affiliate=affiliate, customer=customer, paid_usd="100.00"
        )
        service = AffiliateService(session)
        request = await service.request_payout(
            application=application,
            currency="LTC",
            network="litecoin",
            destination_address="ltc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
        )
        await service.settle_payout(
            payout_id=request.id,
            admin_user_id=admin.id,
            status="rejected",
            decision_note="That address is not on the Litecoin network.",
        )
        stats = await service.stats(application)
        assert stats.available_usd == Decimal("25.00")


async def test_a_paid_payout_keeps_the_money_spent(test_context) -> None:
    async with test_context["session_factory"]() as session:
        affiliate = await _person(session, name="Faisal", email="faisal@example.test")
        admin = await _person(session, name="Owner", email="owner14@example.test")
        customer = await _person(session, name="Buyer", email="buyer4@example.test")
        application = await _approved(session, user=affiliate, admin=admin)
        await _converted_referral(
            session, affiliate=affiliate, customer=customer, paid_usd="100.00"
        )
        service = AffiliateService(session)
        request = await service.request_payout(
            application=application,
            currency="XLM",
            network="stellar",
            destination_address="GABCDEFGHIJKLMNOPQRSTUVWXYZ234567ABCDEFGHIJKLMNOPQRS",
        )
        settled = await service.settle_payout(
            payout_id=request.id,
            admin_user_id=admin.id,
            status="paid",
            transaction_reference="0xabc123",
        )
        assert settled.status == "paid"
        assert settled.transaction_reference == "0xabc123"
        # The amount is a receipt and is never recalculated.
        assert settled.amount_usd == Decimal("25.00")
        stats = await service.stats(application)
        assert stats.available_usd == Decimal("0.00")


async def test_a_payout_cannot_be_settled_twice(test_context) -> None:
    async with test_context["session_factory"]() as session:
        affiliate = await _person(session, name="Noor", email="noor@example.test")
        admin = await _person(session, name="Owner", email="owner15@example.test")
        customer = await _person(session, name="Buyer", email="buyer5@example.test")
        application = await _approved(session, user=affiliate, admin=admin)
        await _converted_referral(
            session, affiliate=affiliate, customer=customer, paid_usd="100.00"
        )
        service = AffiliateService(session)
        request = await service.request_payout(
            application=application,
            currency="USDC",
            network="arbitrum",
            destination_address="0x00112233445566778899aabbccddeeff00112233",
        )
        await service.settle_payout(
            payout_id=request.id, admin_user_id=admin.id, status="paid"
        )
        with pytest.raises(AffiliateError) as error:
            await service.settle_payout(
                payout_id=request.id, admin_user_id=admin.id, status="rejected"
            )
        assert error.value.code == "already_settled"


@pytest.mark.parametrize(
    "currency,network",
    [("USDC", "litecoin"), ("BNB", "trc20"), ("LTC", "stellar"), ("nope", "bep20")],
)
async def test_a_pair_the_catalogue_does_not_offer_is_refused(
    test_context, currency: str, network: str
) -> None:
    async with test_context["session_factory"]() as session:
        affiliate = await _person(
            session, name="Pair", email=f"pair-{currency}-{network}@example.test"
        )
        admin = await _person(
            session, name="Owner", email=f"owner-{currency}-{network}@example.test"
        )
        customer = await _person(
            session, name="Buyer", email=f"buyer-{currency}-{network}@example.test"
        )
        application = await _approved(session, user=affiliate, admin=admin)
        await _converted_referral(
            session, affiliate=affiliate, customer=customer, paid_usd="100.00"
        )
        with pytest.raises(AffiliateError) as error:
            await AffiliateService(session).request_payout(
                application=application,
                currency=currency,
                network=network,
                destination_address="0x00112233445566778899aabbccddeeff00112233",
            )
        assert error.value.code == "unsupported_destination"


async def test_something_that_is_not_an_address_is_refused(test_context) -> None:
    """An address with one wrong character loses the money, so a blank one must not pass."""

    async with test_context["session_factory"]() as session:
        affiliate = await _person(session, name="Adам", email="adam@example.test")
        admin = await _person(session, name="Owner", email="owner16@example.test")
        customer = await _person(session, name="Buyer", email="buyer6@example.test")
        application = await _approved(session, user=affiliate, admin=admin)
        await _converted_referral(
            session, affiliate=affiliate, customer=customer, paid_usd="100.00"
        )
        with pytest.raises(AffiliateError) as error:
            await AffiliateService(session).request_payout(
                application=application,
                currency="USDT",
                network="bep20",
                destination_address="   ",
            )
        assert error.value.code == "invalid_address"


async def test_payouts_are_only_for_approved_affiliates(test_context) -> None:
    async with test_context["session_factory"]() as session:
        user = await _person(session, name="Waiting", email="waiting@example.test")
        service = AffiliateService(session)
        await service.apply(
            user_id=user.id,
            display_name="Waiting",
            social_links=["https://x.com/waiting"],
            requested_discount_code="waiting",
        )
        application = await service.application_for(user.id)
        with pytest.raises(AffiliateError) as error:
            await service.request_payout(
                application=application,
                currency="USDT",
                network="bep20",
                destination_address="0x00112233445566778899aabbccddeeff00112233",
            )
        assert error.value.code == "not_an_affiliate"


# ── What the System Brain reads ──────────────────────────────────────────────────


async def test_the_admin_list_carries_the_applicant_address(test_context) -> None:
    """`User` has no email column; the address lives on the identity beside it.

    An administrator deciding who represents the product has to see who they are
    deciding about, and a template reaching for ``user.email`` renders an empty cell
    rather than raising.
    """

    async with test_context["session_factory"]() as session:
        user = await _person(session, name="Sami", email="sami@example.test")
        service = AffiliateService(session)
        await service.apply(
            user_id=user.id,
            display_name="Sami",
            social_links=["https://x.com/sami"],
            requested_discount_code="sami",
        )
        rows = await service.pending_applications()
        assert len(rows) == 1
        assert rows[0].email == "sami@example.test"
        assert rows[0].application.requested_commission_percent == DEFAULT_COMMISSION_PERCENT


async def test_the_minimum_payout_is_the_one_the_page_promises() -> None:
    """One constant, so the page, the validator and the email cannot disagree."""

    assert Decimal("5.00") == MINIMUM_PAYOUT_USD


def test_the_default_share_is_stated_once() -> None:
    assert Decimal("25") == DEFAULT_COMMISSION_PERCENT


def test_now_is_timezone_aware() -> None:
    """Guards the helpers above: a naive stamp would compare wrongly against stored rows."""

    assert datetime.now(UTC).tzinfo is not None
