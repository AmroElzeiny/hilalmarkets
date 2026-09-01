"""The affiliate journey over HTTP, the way a person and an administrator walk it.

`tests/unit/test_invariant_affiliate_programme.py` holds the rules. This file holds the
*journey*: a form is filled in a browser, a message is queued, an administrator opens the
System Brain and decides, and the page the applicant reloads says something different.

Each of those is a place the pieces are joined, and joins are what break. A service that
works and a route that never calls it is a feature nobody can use.
"""

from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy import select

from ai_market_monitor.db.models import (
    AccountEmailDelivery,
    AffiliateApplication,
    ReferralRelationship,
    User,
)


async def _signup(test_context, email: str, name: str = "Affiliate Tester") -> None:
    client = test_context["client"]
    response = await client.post(
        "/signup/password",
        data={
            "email": email,
            "display_name": name,
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await client.post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def _apply(test_context, **overrides) -> None:
    form = {
        "display_name": "Amina Yusuf",
        "social_links": "https://instagram.com/amina\nhttps://x.com/amina",
        "requested_discount_code": "amina",
        "applicant_note": "I post about halal investing.",
    }
    form.update(overrides)
    response = await test_context["client"].post(
        "/dashboard/affiliate/apply", data=form, follow_redirects=False
    )
    assert response.status_code in {302, 303}, response.text
    return response


async def test_the_old_referrals_address_still_reaches_the_page(test_context) -> None:
    """The link is in sent email and in bookmarks, and neither can be corrected later."""

    await _signup(test_context, "old-address@example.com")
    response = await test_context["client"].get(
        "/dashboard/referrals", follow_redirects=False
    )
    assert response.status_code in {302, 303, 307, 308}
    assert response.headers["location"].endswith("/dashboard/affiliate")


async def test_the_page_offers_the_form_and_states_the_fixed_share(test_context) -> None:
    await _signup(test_context, "form@example.com")
    response = await test_context["client"].get("/dashboard/affiliate")
    assert response.status_code == 200
    assert "Ask to become an affiliate" in response.text
    assert "24 hours" in response.text

    # The share is shown as a fact, with no control to change it...
    assert ">25%<" in response.text
    assert "requested_commission_percent" not in response.text
    # ...and the page says who to write to for more than that.
    assert "office@hilalmarkets.com" in response.text
    assert "Want a bigger share" in response.text


async def test_a_hand_made_form_cannot_award_itself_a_bigger_share(
    test_context,
) -> None:
    """The real attack is not the browser. It is a POST written by hand.

    Removing the box stops an ordinary person; it does nothing about somebody sending the
    field anyway. The route reads no such field, so the value has nowhere to land, and the
    stored share is the standard one whatever the request claimed.
    """

    await _signup(test_context, "crafted@example.com", name="Amina Yusuf")
    redirect = await _apply(test_context, requested_commission_percent="95")
    assert "affiliate_application_sent" in redirect.headers["location"]

    page = await test_context["client"].get("/dashboard/affiliate")
    assert page.status_code == 200
    assert ">25%<" in page.text
    assert ">95%<" not in page.text


async def test_applying_stores_it_queues_an_email_and_changes_the_page(
    test_context,
) -> None:
    """One press of the button has to do all three, or the person is left guessing."""

    await _signup(test_context, "apply@example.com", name="Amina Yusuf")
    redirect = await _apply(test_context)
    assert "affiliate_application_sent" in redirect.headers["location"]

    async with test_context["session_factory"]() as session:
        application = await session.scalar(select(AffiliateApplication))
        assert application is not None
        assert application.status == "pending"
        assert application.requested_discount_code == "AMINA"
        assert application.social_links == [
            "https://instagram.com/amina",
            "https://x.com/amina",
        ]
        delivery = await session.scalar(
            select(AccountEmailDelivery).where(
                AccountEmailDelivery.template_kind == "affiliate_application_received"
            )
        )
        assert delivery is not None
        assert delivery.recipient == "apply@example.com"
        assert delivery.payload_redacted["requested_code"] == "AMINA"

    page = await test_context["client"].get("/dashboard/affiliate")
    assert "Your application is being read" in page.text
    assert "Ask to become an affiliate" not in page.text


async def test_a_bad_link_is_refused_with_words_a_beginner_can_act_on(
    test_context,
) -> None:
    await _signup(test_context, "badlink@example.com")
    redirect = await _apply(test_context, social_links="my instagram")
    assert "error=invalid_link" in redirect.headers["location"]

    page = await test_context["client"].get("/dashboard/affiliate?error=invalid_link")
    assert "not a web address" in page.text
    # And the form is still there to correct.
    assert "Ask to become an affiliate" in page.text


async def test_the_email_that_goes_out_carries_the_real_numbers(test_context) -> None:
    """The renderer is exercised, not just the queue row: a template that raises would
    fail the delivery quietly at send time rather than here."""

    from ai_market_monitor.services.account_emails import AccountEmailOutboxService

    await _signup(test_context, "render@example.com", name="Amina Yusuf")
    await _apply(test_context)
    async with test_context["session_factory"]() as session:
        delivery = await session.scalar(
            select(AccountEmailDelivery).where(
                AccountEmailDelivery.template_kind == "affiliate_application_received"
            )
        )
        rendered = AccountEmailOutboxService(
            session, test_context["settings"]
        )._render(delivery)
    assert "affiliate application" in rendered.subject.lower()
    assert "AMINA" in rendered.html_body
    assert "24 hours" in rendered.html_body
    assert "Assalamu Alaikum Amina" in rendered.text_body


async def test_the_receipt_is_actually_sent_not_only_queued(test_context) -> None:
    """A queued row is not a message somebody received.

    The outbox is swept every minute, so a queued row would eventually go out — but the
    person who just pressed the button should not wait for a sweep, and a renderer that
    raises would only be discovered then. The send is attempted at once, after the
    commit, and a provider failure leaves the row for the sweep rather than turning a
    submitted application into an error page.
    """

    await _signup(test_context, "sent@example.com", name="Amina Yusuf")
    await _apply(test_context)

    async with test_context["session_factory"]() as session:
        delivery = await session.scalar(
            select(AccountEmailDelivery).where(
                AccountEmailDelivery.template_kind == "affiliate_application_received"
            )
        )
        assert delivery.status == "sent", (
            f"the receipt is still {delivery.status}: {delivery.last_error}"
        )

    posted = [
        message
        for message in test_context["settings"].email_test_outbox
        if message.get("recipient") == "sent@example.com"
        and "affiliate" in str(message.get("subject", "")).lower()
    ]
    assert posted, "no affiliate message reached the outbox"


async def _admin(test_context, email: str = "affiliate-admin@hilalmarkets.test") -> User:
    from ai_market_monitor.db.models import UserIdentity
    from ai_market_monitor.db.models.enums import IdentityProvider, UserRole

    async with test_context["session_factory"]() as session:
        user = User(display_name="Programme owner", role=UserRole.ADMIN)
        session.add(user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject=email,
                normalized_identifier=email,
                display_identifier=email,
                is_verified=True,
                is_primary=True,
            )
        )
        await session.commit()
        return user


def _csrf(text: str) -> str:
    found = re.search(r'name="csrf_token" value="([a-f0-9]+)"', text)
    assert found is not None, "the decision form carries no form token"
    return found.group(1)


class _as_admin:
    """Act as the administrator on the same client, then hand it back to the applicant.

    The applicant's session cookie outranks the ``X-User-ID`` header the System Brain
    accepts in tests, so a request made without clearing it is made *as the applicant* —
    and the route correctly answers "Administrator role required". Setting the cookies
    aside for the duration is what makes the two roles distinguishable on one client, and
    putting them back is what lets the test go on to reload the affiliate's own page.
    """

    def __init__(self, test_context, admin: User) -> None:
        self._client = test_context["client"]
        self._admin = admin
        self._saved: dict[str, str] = {}

    def __enter__(self) -> dict[str, str]:
        self._saved = dict(self._client.cookies)
        self._client.cookies.clear()
        return {"X-User-ID": str(self._admin.id)}

    def __exit__(self, *_: object) -> None:
        self._client.cookies.clear()
        for name, value in self._saved.items():
            self._client.cookies.set(name, value)


async def test_the_system_brain_shows_the_application_with_the_applicants_address(
    test_context,
) -> None:
    """An administrator deciding who represents the product must see who they are."""

    await _signup(test_context, "seen@example.com", name="Amina Yusuf")
    await _apply(test_context)
    admin = await _admin(test_context)

    with _as_admin(test_context, admin) as headers:
        page = await test_context["client"].get(
            "/dashboard/system-brain/affiliate", headers=headers
        )
    assert page.status_code == 200
    assert 'data-testid="affiliate-application"' in page.text
    assert "seen@example.com" in page.text
    # What they asked for is shown as a request, never pre-filled into the boxes.
    assert "Code asked for" in page.text
    assert 'name="discount_percent"' in page.text
    assert 'value="AMINA"' not in page.text
    # The share is not something they asked for, so it is not labelled as if it were.
    assert "Share asked for" not in page.text
    assert "Applied on" in page.text
    # This screen is the only place the share can be moved, and the box is here...
    assert 'name="commission_percent"' in page.text
    # ...while the applicant's own page offers no such control at all.
    assert "requested_commission_percent" not in page.text
    # The default is stated on the label, so leaving the box alone is a decision.
    assert "Empty uses 25%." in page.text


async def test_an_administrator_approves_through_the_real_route(test_context) -> None:
    """Form token, service, email and redirect — the whole button, over HTTP."""

    await _signup(test_context, "approve-me@example.com", name="Amina Yusuf")
    await _apply(test_context)
    admin = await _admin(test_context, email="approver@hilalmarkets.test")

    async with test_context["session_factory"]() as session:
        application = await session.scalar(select(AffiliateApplication))

    with _as_admin(test_context, admin) as headers:
        page = await test_context["client"].get(
            "/dashboard/system-brain/affiliate", headers=headers
        )
        token = _csrf(page.text)
        posted = await test_context["client"].post(
            f"/dashboard/system-brain/affiliate/{application.id}/approve",
            data={
                "csrf_token": token,
                "discount_code": "AMINA10",
                "discount_percent": "15",
                "commission_percent": "30",
            },
            headers=headers,
            follow_redirects=False,
        )
    assert posted.status_code == 303, posted.text
    assert "success=" in posted.headers["location"]

    async with test_context["session_factory"]() as session:
        decided = await session.scalar(select(AffiliateApplication))
        assert decided.status == "approved"
        assert decided.discount_code == "AMINA10"
        assert decided.discount_percent == Decimal("15.00")
        assert decided.commission_percent == Decimal("30.00")
        assert decided.decided_by_user_id == admin.id
        delivery = await session.scalar(
            select(AccountEmailDelivery).where(
                AccountEmailDelivery.template_kind == "affiliate_application_approved"
            )
        )
        assert delivery is not None
        assert delivery.recipient == "approve-me@example.com"
        assert delivery.payload_redacted["discount_code"] == "AMINA10"
        assert delivery.payload_redacted["commission_percent"] == "30"


async def test_a_decision_without_a_form_token_is_refused(test_context) -> None:
    """The button is a state change, so it carries the same guard as every other one."""

    await _signup(test_context, "notoken@example.com", name="Amina Yusuf")
    await _apply(test_context)
    admin = await _admin(test_context, email="notoken-admin@hilalmarkets.test")
    async with test_context["session_factory"]() as session:
        application = await session.scalar(select(AffiliateApplication))

    with _as_admin(test_context, admin) as headers:
        posted = await test_context["client"].post(
            f"/dashboard/system-brain/affiliate/{application.id}/approve",
            data={"csrf_token": "0" * 64, "discount_percent": "10"},
            headers=headers,
            follow_redirects=False,
        )
    assert posted.status_code in {400, 403}
    async with test_context["session_factory"]() as session:
        untouched = await session.scalar(select(AffiliateApplication))
        assert untouched.status == "pending"


async def test_a_signed_in_person_cannot_reach_the_affiliate_admin(test_context) -> None:
    """It decides money, so an ordinary account may not open it."""

    await _signup(test_context, "nosy@example.com")
    refused = await test_context["client"].get(
        "/dashboard/system-brain/affiliate", follow_redirects=False
    )
    assert refused.status_code in {302, 303, 307, 401, 403, 404}


async def test_a_payout_is_settled_through_the_real_route(test_context) -> None:
    from ai_market_monitor.db.models import AffiliatePayoutRequest
    from ai_market_monitor.services.affiliate import AffiliateService

    await _signup(test_context, "settle@example.com", name="Amina Yusuf")
    await _apply(test_context)
    admin = await _admin(test_context, email="settler@hilalmarkets.test")

    async with test_context["session_factory"]() as session:
        application = await session.scalar(select(AffiliateApplication))
        customer = User(display_name="Buyer")
        session.add(customer)
        await session.flush()
        await AffiliateService(session).approve(
            application_id=application.id,
            admin_user_id=admin.id,
            discount_percent="10",
        )
        session.add(
            ReferralRelationship(
                referrer_user_id=application.user_id,
                referred_user_id=customer.id,
                status="paid_converted",
                reward_status="eligible_after_first_paid_month",
                metadata_json={"paid_amount_usd": "100.00"},
            )
        )
        await session.commit()

    await test_context["client"].post(
        "/dashboard/affiliate/payout",
        data={
            "currency": "TRX",
            "network": "trc20",
            "destination_address": "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE",
        },
        follow_redirects=False,
    )

    async with test_context["session_factory"]() as session:
        payout = await session.scalar(select(AffiliatePayoutRequest))

    with _as_admin(test_context, admin) as headers:
        page = await test_context["client"].get(
            "/dashboard/system-brain/affiliate", headers=headers
        )
        assert 'data-testid="affiliate-payout"' in page.text
        assert "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE" in page.text
        assert "Tron (TRC20)" in page.text
        token = _csrf(page.text)

        posted = await test_context["client"].post(
            f"/dashboard/system-brain/affiliate/payouts/{payout.id}/settle",
            data={
                "csrf_token": token,
                "status": "paid",
                "transaction_reference": "0xdeadbeef",
            },
            headers=headers,
            follow_redirects=False,
        )
    assert posted.status_code == 303, posted.text

    async with test_context["session_factory"]() as session:
        settled = await session.scalar(select(AffiliatePayoutRequest))
        assert settled.status == "paid"
        assert settled.transaction_reference == "0xdeadbeef"
        assert settled.decided_by_user_id == admin.id

    # And the affiliate's own page says so.
    after = await test_context["client"].get("/dashboard/affiliate")
    assert "Paid" in after.text


async def test_approval_writes_the_programme_and_the_page_shows_it(test_context) -> None:
    """The whole join: apply → approve in the System Brain → the affiliate's own page."""

    from ai_market_monitor.services.affiliate import AffiliateService

    await _signup(test_context, "journey@example.com", name="Amina Yusuf")
    await _apply(test_context)

    async with test_context["session_factory"]() as session:
        application = await session.scalar(select(AffiliateApplication))
        admin = User(display_name="Owner")
        session.add(admin)
        await session.flush()
        approved = await AffiliateService(session).approve(
            application_id=application.id,
            admin_user_id=admin.id,
            discount_code="AMINA10",
            discount_percent="10",
            commission_percent=None,
        )
        await session.commit()
        assert approved.commission_percent == Decimal("25.00")

    page = await test_context["client"].get("/dashboard/affiliate")
    assert page.status_code == 200
    assert "AMINA10" in page.text
    # The two numbers that decide everything after this, in the page's own words.
    assert "saves" in page.text
    assert "10%" in page.text
    assert "25%" in page.text
    # Nothing to take out yet, and the page says so rather than offering the button.
    assert "You can ask for a payout once you have $5.00" in page.text


async def test_an_approved_affiliate_sees_names_and_can_ask_for_a_payout(
    test_context,
) -> None:
    from ai_market_monitor.services.affiliate import AffiliateService

    await _signup(test_context, "paid@example.com", name="Amina Yusuf")
    await _apply(test_context)

    async with test_context["session_factory"]() as session:
        application = await session.scalar(select(AffiliateApplication))
        admin = User(display_name="Owner")
        customer = User(display_name="Karim Hassan")
        session.add_all([admin, customer])
        await session.flush()
        await AffiliateService(session).approve(
            application_id=application.id,
            admin_user_id=admin.id,
            discount_percent="10",
        )
        session.add(
            ReferralRelationship(
                referrer_user_id=application.user_id,
                referred_user_id=customer.id,
                status="paid_converted",
                reward_status="eligible_after_first_paid_month",
                metadata_json={"paid_amount_usd": "100.00"},
            )
        )
        await session.commit()

    page = await test_context["client"].get("/dashboard/affiliate")
    assert "Karim Hassan" in page.text
    # The name, never the address.
    assert "@example.com" not in page.text.split("Karim Hassan")[1][:400]
    assert "$25.00" in page.text

    requested = await test_context["client"].post(
        "/dashboard/affiliate/payout",
        data={
            "currency": "USDT",
            "network": "bep20",
            "destination_address": "0x00112233445566778899aabbccddeeff00112233",
        },
        follow_redirects=False,
    )
    assert "affiliate_payout_requested" in requested.headers["location"]

    after = await test_context["client"].get("/dashboard/affiliate")
    assert "Waiting" in after.text
    assert "BNB Smart Chain (BEP20)" in after.text


async def test_a_refusal_reopens_the_form_and_queues_the_reason(test_context) -> None:
    from ai_market_monitor.services.affiliate import AffiliateService

    await _signup(test_context, "refused@example.com", name="Amina Yusuf")
    await _apply(test_context)

    async with test_context["session_factory"]() as session:
        application = await session.scalar(select(AffiliateApplication))
        admin = User(display_name="Owner")
        session.add(admin)
        await session.flush()
        await AffiliateService(session).reject(
            application_id=application.id,
            admin_user_id=admin.id,
            decision_note="Tell us more about your audience first.",
        )
        await session.commit()

    page = await test_context["client"].get("/dashboard/affiliate")
    assert "Not approved this time" in page.text
    assert "Tell us more about your audience first." in page.text
    # And the form is open again, carrying what they wrote last time.
    assert "Apply again" in page.text
    assert "AMINA" in page.text
    # One link per line, as they typed them. Jinja's `join('\n')` really does produce a
    # newline — if it produced the two characters instead, a person re-applying would see
    # their links run together and the form would refuse what it had just handed them.
    assert "https://instagram.com/amina\nhttps://x.com/amina" in page.text


async def test_the_payout_form_offers_only_coins_the_catalogue_holds(
    test_context,
) -> None:
    """The page and the validator read the same list, so neither can offer more."""

    from ai_market_monitor.services.affiliate import AffiliateService
    from ai_market_monitor.services.affiliate_payout_options import PAYOUT_CURRENCIES

    await _signup(test_context, "options@example.com", name="Amina Yusuf")
    await _apply(test_context)
    async with test_context["session_factory"]() as session:
        application = await session.scalar(select(AffiliateApplication))
        admin = User(display_name="Owner")
        customer = User(display_name="Buyer")
        session.add_all([admin, customer])
        await session.flush()
        await AffiliateService(session).approve(
            application_id=application.id,
            admin_user_id=admin.id,
            discount_percent="10",
        )
        session.add(
            ReferralRelationship(
                referrer_user_id=application.user_id,
                referred_user_id=customer.id,
                status="paid_converted",
                reward_status="eligible_after_first_paid_month",
                metadata_json={"paid_amount_usd": "100.00"},
            )
        )
        await session.commit()

    page = await test_context["client"].get("/dashboard/affiliate")
    for currency in PAYOUT_CURRENCIES:
        assert f'value="{currency.key}"' in page.text, f"{currency.key} is not offered"
    # And the way out for somebody who wants paying differently.
    assert "office@hilalmarkets.com" in page.text
