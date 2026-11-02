from __future__ import annotations

import asyncio
import os

import asyncpg

from bot.db import migration_sql


async def migrate() -> None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not available.")
    connection = await asyncpg.connect(database_url, command_timeout=60)
    try:
        await connection.execute(migration_sql())
    finally:
        await connection.close()
    print("Gemini Store schema is ready.")


if __name__ == "__main__":
    asyncio.run(migrate())
