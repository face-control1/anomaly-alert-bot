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

from config import BOT_TOKEN, LOG_LEVEL, LOG_FILE, ADMIN_IDS, SCAN_INTERVAL
from database.db import init_db, get_all_active_user_ids, log_alert
from bot.handlers import router
from market.scanner import MarketScanner
from market.anomaly import Alert
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


async def make_alert_sender(bot: Bot):
    """Возвращает async callback, который рассылает алерты всем активным подписчикам."""
    logger = logging.getLogger("alert_sender")

    async def send_alerts(alerts: list[Alert]) -> None:
        if not alerts:
            return
        user_ids = get_all_active_user_ids()
        if not user_ids:
            return

        for alert in alerts:
            # Логируем алерт
            try:
                log_alert(alert.ticker, alert.exchange, alert.alert_type, alert.headline)
            except Exception:
                pass

            text = alert.format_message()

            for uid in user_ids:
                try:
                    await bot.send_message(uid, text, parse_mode="HTML")
                except Exception as e:
                    logger.debug("Не удалось отправить алерт пользователю %d: %s", uid, e)

    return send_alerts


async def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    keep_alive()  # HTTP-сервер для Render/UptimeRobot

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        logger.error("❌ Укажите BOT_TOKEN в .env файле!")
        sys.exit(1)

    logger.info("Инициализация базы данных...")
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Создаём callback для рассылки алертов
    send_alerts = await make_alert_sender(bot)

    # Запускаем сканер рынка
    scanner = MarketScanner(
        alert_callback=send_alerts,
        interval_seconds=SCAN_INTERVAL,
    )
    scanner.start()
    logger.info("Сканер рынка запущен (интервал %d сек)", SCAN_INTERVAL)

    logger.info("🚀 Бот запущен!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scanner.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
