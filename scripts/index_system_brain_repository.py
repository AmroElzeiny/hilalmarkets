"""Refresh the privacy-filtered repository evidence index outside interactive turns."""

from __future__ import annotations

import asyncio

from ai_market_monitor.core.database import SessionFactory
from ai_market_monitor.services.system_brain_repository_index import (
    RepositoryEvidenceIndexService,
)


async def main() -> None:
    async with SessionFactory() as session:
        result = await RepositoryEvidenceIndexService().refresh(session)
        await session.commit()
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
