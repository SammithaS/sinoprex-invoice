"""Seed the database configured in DATABASE_URL with the mock company's data.

uv run python -m scripts.seed
"""

import asyncio

from app.db.seed import seed_reference_data
from app.db.session import get_engine, get_sessionmaker


async def main() -> None:
    async with get_sessionmaker()() as session:
        counts = await seed_reference_data(session)
    await get_engine().dispose()
    print("seeded:", ", ".join(f"{n} {k}" for k, n in counts.items()))


if __name__ == "__main__":
    asyncio.run(main())
