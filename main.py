"""
Точка входа: запускает бота и фоновый сканер рынка.
"""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database.db import init_db
from bot.handlers import router
from scanner.market_scanner import MarketScanner
from database.db import (
    get_active_subscribers,
    get_favorites,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def send_alerts(bot: Bot, alerts: list) -> None:
    """Отправляет алерты всем подписчикам (c фильтром избранных)."""
    if not alerts:
        return
    user_ids = get_active_subscribers()
    if not user_ids:
        return
    for alert in alerts:
        text = (
            f"🚨 <b>{alert.ticker}</b> [{alert.exchange}]\n"
            f"📊 Тип: {alert.alert_type}\n"
            f"💰 Цена: {alert.price:.2f}\n"
            f"📈 Изменение: {alert.price_change:+.2f}%\n"
            f"📊 Объем: {alert.volume:,}\n"
            f"🕔 {alert.timestamp}"
        )
        for uid in user_ids:
            try:
                # Если у пользователя есть избранное — шлём только алерты по его акциям
                favs = get_favorites(uid)
                if favs:
                    is_in_favs = any(
                        f["ticker"] == alert.ticker and f["exchange"] == alert.exchange
                        for f in favs
                    )
                    if not is_in_favs:
                        continue
                await bot.send_message(uid, text, parse_mode="HTML")
            except Exception as e:
                logger.debug("Не удалось отправить алерт пользователю %d: %s", uid, e)


async def main() -> None:
    init_db()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    scanner = MarketScanner(callback=lambda alerts: asyncio.create_task(send_alerts(bot, alerts)))

    logger.info("Запуск бота...")
    await asyncio.gather(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
        scanner.run(),
    )


if __name__ == "__main__":
    asyncio.run(main())
