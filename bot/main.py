from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import Settings
from bot.db import Database
from bot.handlers.admin import router as admin_router
from bot.handlers.user import router as user_router


async def main_async() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    settings = Settings.from_env()
    db = await Database.connect(settings.database_url)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(user_router)
    dispatcher.include_router(admin_router)

    @dispatcher.errors()
    async def on_error(event, exception: Exception) -> bool:
        logging.getLogger(__name__).error(
            "Unhandled Telegram update error: %r",
            exception,
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        return True

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        logging.getLogger(__name__).info("Gemini Store bot started with durable PostgreSQL storage.")
        await dispatcher.start_polling(bot, db=db, settings=settings, close_bot_session=True)
    finally:
        await db.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
