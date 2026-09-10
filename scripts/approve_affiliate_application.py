"""Approve one affiliate application from the command line.

This is the same decision the System Brain page makes, in the same order: the row is
written and committed first, and only then is the approval email attempted. A message
sent before the row is written is a message that can describe an approval that did not
happen.

It exists because the button needs an admin browser session behind Cloudflare Access,
and an operator on the server has neither. It never invents a value the page would have
demanded: the discount is required here exactly as it is required there, and the
administrator whose name goes on the decision must be named.

Usage (on the server, inside the app container):

    python scripts/approve_affiliate_application.py \
        --code AMRM --discount 20 --admin-email you@example.com

Add --dry-run first to see what would happen without writing anything.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from sqlalchemy import func, select

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.database import SessionFactory
from ai_market_monitor.db.models import AffiliateApplication, User, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
from ai_market_monitor.services.affiliate import (
    AffiliateError,
    AffiliateService,
    enqueue_affiliate_email,
    first_name_of,
    try_sending_now,
)
from ai_market_monitor.services.affiliate_attribution import REFERRAL_LINK_QUERY_KEY
from ai_market_monitor.services.affiliate_payout_options import MINIMUM_PAYOUT_USD


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--code", help="The discount code the applicant asked for.")
    target.add_argument("--application-id", help="The application's own id.")
    parser.add_argument(
        "--discount",
        required=True,
        help="Percent off for the customer. Required, exactly as on the page.",
    )
    parser.add_argument(
        "--admin-email",
        required=True,
        help="Verified email of the administrator making this decision.",
    )
    parser.add_argument("--commission", default="", help="Blank means the default share.")
    parser.add_argument(
        "--subsequent-commission",
        default="",
        help="Blank means the same share as the first payment.",
    )
    parser.add_argument("--note", default="", help="Optional private note.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be approved, write nothing, send nothing.",
    )
    return parser.parse_args(argv)


async def _find_admin_user_id(session, email: str) -> UUID:
    """Resolve the deciding administrator, and refuse anything less than certain."""

    normalized = email.strip().lower()
    user = await session.scalar(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(
            UserIdentity.provider == IdentityProvider.EMAIL,
            UserIdentity.is_verified.is_(True),
            func.lower(UserIdentity.normalized_identifier) == normalized,
        )
        .limit(1)
    )
    if user is None:
        raise SystemExit(f"No account with a verified email {email}.")
    if user.role != UserRole.ADMIN:
        raise SystemExit(f"{email} is not an administrator, so it cannot decide this.")
    return user.id


async def _find_application(session, args: argparse.Namespace) -> AffiliateApplication:
    if args.application_id:
        application = await session.get(AffiliateApplication, UUID(args.application_id))
        if application is None:
            raise SystemExit("No application with that id.")
        return application

    wanted = args.code.strip().upper()
    matches = list(
        await session.scalars(
            select(AffiliateApplication).where(
                func.upper(AffiliateApplication.requested_discount_code) == wanted
            )
        )
    )
    if not matches:
        raise SystemExit(f"No application asked for the code {wanted}.")
    if len(matches) > 1:
        ids = ", ".join(str(row.id) for row in matches)
        raise SystemExit(
            f"{len(matches)} applications asked for {wanted}. "
            f"Re-run with --application-id, one of: {ids}"
        )
    return matches[0]


async def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    async with SessionFactory() as session:
        application = await _find_application(session, args)
        admin_user_id = await _find_admin_user_id(session, args.admin_email)

        print(f"Application : {application.id}")
        print(f"Status now  : {application.status}")
        print(f"Asked for   : {application.requested_discount_code}")
        print(f"Discount    : {args.discount}%")
        print(f"Commission  : {args.commission or 'default'}")
        if args.dry_run:
            print("Dry run. Nothing was written and no email was sent.")
            return

        try:
            approved = await service_approve(session, application, admin_user_id, args)
        except AffiliateError as exc:
            await session.rollback()
            raise SystemExit(f"Refused: {exc}") from exc

        print(f"{approved.discount_code} is live. The affiliate has been emailed.")


async def service_approve(session, application, admin_user_id, args):
    """Approve, queue the email, commit, then attempt the send. In that order."""

    service = AffiliateService(session)
    approved = await service.approve(
        application_id=application.id,
        admin_user_id=admin_user_id,
        discount_code=args.code or None,
        discount_percent=args.discount,
        commission_percent=args.commission or None,
        subsequent_commission_percent=args.subsequent_commission or None,
    )
    delivery = None
    applicant = await session.get(User, approved.user_id)
    settings = get_settings()
    if applicant is not None:
        base = str(settings.public_base_url).rstrip("/")
        delivery = await enqueue_affiliate_email(
            session,
            user_id=approved.user_id,
            template_kind="affiliate_application_approved",
            event_key=f"affiliate-approved:{approved.id}",
            payload={
                "first_name": first_name_of(applicant, prefer=approved.display_name),
                "discount_code": approved.discount_code,
                "discount_percent": f"{approved.discount_percent:.0f}",
                "commission_percent": f"{approved.commission_percent:.0f}",
                "subsequent_commission_percent": (
                    f"{service.rates_for(approved).subsequent_percent:.0f}"
                ),
                "referral_url": (
                    f"{base}/signup?{REFERRAL_LINK_QUERY_KEY}={approved.discount_code}"
                ),
                "minimum_payout": f"${MINIMUM_PAYOUT_USD:.2f}",
            },
        )
    await session.commit()
    await try_sending_now(session, settings, delivery)
    if args.note.strip():
        approved.decision_note = args.note.strip()[:2000]
        await session.commit()
    return approved


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
