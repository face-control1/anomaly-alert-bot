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
from database.db import init_db, get_all_active_user_ids, log_alert, get_favorites
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

    return send_alerts


async def make_orderbook_callback(bot: Bot):
    """Callback для крупных заявок в стакане."""
    from market.orderbook_watcher import LargeOrderAlert
    logger = logging.getLogger("orderbook_sender")

    async def send_orderbook_alert(alert: LargeOrderAlert) -> None:
        user_ids = get_all_active_user_ids()
        if not user_ids:
            return

        side_emoji = "🟢" if alert.side == "BID" else "🔴"
        side_label = "покупка (BID)" if alert.side == "BID" else "продажа (ASK)"
        text = (
            f"📊 <b>Крупная заявка в стакане</b>\n\n"
            f"{side_emoji} <b>{alert.ticker}</b> ({alert.exchange}) — {side_label}\n"
            f"💰 Цена: <b>{alert.price:,.2f} ₽</b>\n"
            f"📦 Объём: <b>{alert.quantity:,} лот.</b> "
            f"({alert.value_rub / 1_000_000:.1f} млн ₽)\n"
            f"📈 Превышает средний размер в <b>{alert.multiplier:.1f}×</b>"
        )

        for uid in user_ids:
            try:
                favs = get_favorites(uid)
                if favs:
                    if not any(f["ticker"] == alert.ticker and f["exchange"] == alert.exchange for f in favs):
                        continue
                await bot.send_message(uid, text, parse_mode="HTML")
            except Exception as e:
                logger.debug("Orderbook alert send error user %d: %s", uid, e)

    return send_orderbook_alert


async def make_corp_reports_callback(bot: Bot):
    """Callback для корпоративных отчётов."""
    from market.corp_reports_watcher import CorpReportAlert
    logger = logging.getLogger("corp_reports_sender")

    async def send_corp_report(alert: CorpReportAlert) -> None:
        user_ids = get_all_active_user_ids()
        if not user_ids:
            return

        priority_badge = "⭐ " if alert.is_priority else ""
        text = (
            f"🏢 <b>Корпоративный отчёт</b>\n\n"
            f"{priority_badge}<b>{alert.ticker}</b> — {alert.company_name}\n"
            f"{alert.report_type}\n"
            f"📅 Дата: {alert.report_date}\n"
        )
        if alert.description and alert.description != "—":
            text += f"📝 {alert.description}\n"
        if alert.url:
            text += f"\n🔗 <a href='{alert.url}'>Открыть отчёт</a>"

        for uid in user_ids:
            try:
                favs = get_favorites(uid)
                if favs:
                    if not any(f["ticker"] == alert.ticker and f["exchange"] == alert.exchange for f in favs):
                        continue
                await bot.send_message(uid, text, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as e:
                logger.debug("Corp report send error user %d: %s", uid, e)

    return send_corp_report


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

    # Создаём callbacks для рассылки
    send_alerts = await make_alert_sender(bot)
    send_orderbook = await make_orderbook_callback(bot)
    send_corp_reports = await make_corp_reports_callback(bot)

    # Запускаем основной сканер рынка
    scanner = MarketScanner(
        alert_callback=send_alerts,
        interval_seconds=SCAN_INTERVAL,
    )
    scanner.start()
    logger.info("Сканер рынка запущен (интервал %d сек)", SCAN_INTERVAL)

    # Запускаем детектор крупных заявок
    orderbook_watcher = None
    try:
        from market.orderbook_watcher import OrderbookWatcher
        from market.moex import get_all_moex_tickers

        tickers_raw = await asyncio.get_event_loop().run_in_executor(None, get_all_moex_tickers)
        ob_tickers = [
            {"ticker": t.get("ticker", ""), "exchange": "MOEX", "board": t.get("board", "TQBR")}
            for t in tickers_raw[:100]  # ограничиваем нагрузку
        ]
        if ob_tickers:
            orderbook_watcher = OrderbookWatcher(
                tickers=ob_tickers,
                alert_callback=send_orderbook,
                interval_seconds=60,
            )
            await orderbook_watcher.start()
            logger.info("OrderbookWatcher запущен (%d тикеров)", len(ob_tickers))
    except Exception as e:
        logger.warning("OrderbookWatcher не запущен: %s", e)

    # Запускаем мониторинг корпоративных отчётов
    corp_watcher = None
    try:
        from market.corp_reports_watcher import CorpReportsWatcher
        corp_watcher = CorpReportsWatcher(
            alert_callback=send_corp_reports,
            interval_seconds=120,
            priority_only=False,
        )
        await corp_watcher.start()
        logger.info("CorpReportsWatcher запущен")
    except Exception as e:
        logger.warning("CorpReportsWatcher не запущен: %s", e)

    # Запускаем APScheduler для напоминаний о подписке
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from bot.payments_yukassa import setup_expiry_reminders, YUKASSA_AVAILABLE
        if YUKASSA_AVAILABLE:
            scheduler = AsyncIOScheduler()
            setup_expiry_reminders(scheduler, bot)
            scheduler.start()
            logger.info("APScheduler запущен (напоминания о подписке)")
    except ImportError:
        logger.info("APScheduler не установлен — напоминания о подписке отключены")
    except Exception as e:
        logger.warning("APScheduler не запущен: %s", e)

    logger.info("🚀 Бот запущен!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scanner.stop()
        if orderbook_watcher:
            await orderbook_watcher.stop()
        if corp_watcher:
            await corp_watcher.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
