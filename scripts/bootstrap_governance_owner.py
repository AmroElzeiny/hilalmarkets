from __future__ import annotations

import argparse
import asyncio

from ai_market_monitor.core.database import SessionFactory, engine
from ai_market_monitor.services.governance_bootstrap import grant_owner_governance_roles


async def bootstrap(*, email: str, reason: str) -> None:
    async with SessionFactory() as session:
        grants = await grant_owner_governance_roles(
            session,
            email=email,
            reason=reason,
        )
        await session.commit()
    await engine.dispose()
    roles = ", ".join(grant.role for grant in grants)
    print(f"OK: explicit governance grants are active for {email.strip().casefold()}: {roles}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Grant the first verified administrator explicit HilalMarkets governance roles."
        )
    )
    parser.add_argument("--email", required=True, help="Existing verified administrator email.")
    parser.add_argument(
        "--reason",
        required=True,
        help="Audit reason for granting governance authority.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(bootstrap(email=args.email, reason=args.reason))
