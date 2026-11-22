"""
Точка входа: запускает бота и фоновый сканер рынка.
"""
import asyncio
import logging
import sys
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

# Добавляем корневую папку в PYTHONPATH
sys.path.insert(0, os.path.dirname(__file__))

from config import BOT_TOKEN, LOG_LEVEL, LOG_FILE
from database.db import init_db
from bot.handlers import router
from market.scanner import market_scanner_loop
from keep_alive import keep_alive


def setup_logging() -> None:
    os.makedirs(os.path.dirname(LOG_FILE) if os.path.dirname(LOG_FILE) else ".", exist_ok=True)
    handlers = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    except Exception:
        pass
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


async def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    keep_alive()  # HTTP-сервер для Render/UptimeRobot

    if BOT_TOKEN == "YOUR_BOT_TOKEN":
        logger.error("Укажите BOT_TOKEN в .env файле!")
        sys.exit(1)

    logger.info("Инициализация базы данных...")
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Запускаем фоновый сканер
    scanner_task = asyncio.create_task(market_scanner_loop(bot))

    logger.info("Бот запущен!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scanner_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
