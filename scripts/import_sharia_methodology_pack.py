import asyncio
import json

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.database import SessionFactory
from ai_market_monitor.services.sharia_import_pack import (
    ShariaMethodologyImportPackService,
)


async def main() -> None:
    settings = get_settings()
    async with SessionFactory() as session:
        result = await ShariaMethodologyImportPackService(
            session,
            settings,
        ).import_bundle()
        await session.commit()
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
