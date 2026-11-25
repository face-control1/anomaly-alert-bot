"""
Точка входа: запускает бота и фоновый сканер рынка.
"""
import asyncio
import logging
import sys
import os
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile

# Добавляем корневую папку в PYTHONPATH
sys.path.insert(0, os.path.dirname(__file__))

from config import BOT_TOKEN, LOG_LEVEL, LOG_FILE, ADMIN_IDS, SCAN_INTERVAL
from database.db import (
    init_db,
    get_all_active_user_ids,
    log_alert,
    get_favorites,
    get_user_settings,
    get_runtime_config,
)
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
        if not get_runtime_config()["alerts_enabled"]:
            return
        user_ids = get_all_active_user_ids()
        if not user_ids:
            return

        for alert in alerts:
            # Логируем алерт
            try:
                direction = 1 if alert.price_change_1m_pct > 0 else (-1 if alert.price_change_1m_pct < 0 else 0)
                log_alert(
                    alert.ticker,
                    alert.exchange,
                    alert.alert_type,
                    alert.headline,
                    signal_price=alert.current_price,
                    direction=direction,
                    score=alert.score,
                )
            except Exception:
                pass

            text = alert.format_message()
            alert_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📈 Открыть график",
                        url=f"https://www.tradingview.com/chart/?symbol=MOEX%3A{alert.ticker}",
                    ),
                    InlineKeyboardButton(text="🎛 Настройки", callback_data="settings"),
                ],
                [
                    InlineKeyboardButton(text="⭐ В избранное", callback_data=f"quickfav:{alert.ticker}"),
                    InlineKeyboardButton(text="💡 О сигнале", callback_data=f"signalwhy:{alert.ticker}:{alert.score}"),
                ],
            ])

            chart = None
            try:
                from market.moex import get_moex_candles
                from market.chart import render_signal_chart
                chart_candles = await get_moex_candles(alert.ticker, "TQBR", interval=1, n_bars=60)
                chart = render_signal_chart(alert.ticker, chart_candles, alert.current_price)
            except Exception as exc:
                logger.debug("Не удалось построить график %s: %s", alert.ticker, exc)

            for uid in user_ids:
                try:
                    settings = get_user_settings(uid)
                    if settings["paused"]:
                        continue
                    if settings["quiet_hours"]:
                        msk_hour = datetime.now(timezone(timedelta(hours=3))).hour
                        if msk_hour >= 23 or msk_hour < 7:
                            continue
                    type_enabled = {
                        "volume_spike": settings["volume_alerts"],
                        "price_move": settings["price_alerts"],
                        "bollinger": settings["band_alerts"],
                    }
                    if alert.alert_type != "combined" and not type_enabled.get(alert.alert_type, True):
                        continue
                    if alert.score < settings["min_score"]:
                        continue
                    if settings["direction"] == "up" and alert.price_change_1m_pct <= 0:
                        continue
                    if settings["direction"] == "down" and alert.price_change_1m_pct >= 0:
                        continue
                    # Если у пользователя есть избранное — шлём только алерты по его акциям
                    favs = get_favorites(uid)
                    if favs:
                        is_in_favs = any(
                            f["ticker"] == alert.ticker and f["exchange"] == alert.exchange
                            for f in favs
                        )
                        if not is_in_favs:
                            continue
                    if chart:
                        await bot.send_photo(
                            uid,
                            BufferedInputFile(chart, filename=f"{alert.ticker}_signal.png"),
                            caption=text,
                            parse_mode="HTML",
                            reply_markup=alert_kb,
                        )
                    else:
                        await bot.send_message(uid, text, parse_mode="HTML", reply_markup=alert_kb)
                except Exception as e:
                    logger.debug("Не удалось отправить алерт пользователю %d: %s", uid, e)

    return send_alerts


async def make_orderbook_callback(bot: Bot):
    """Callback для крупных заявок в стакане."""
    from market.orderbook_watcher import LargeOrderAlert
    logger = logging.getLogger("orderbook_sender")

    async def send_orderbook_alert(alert: LargeOrderAlert) -> None:
        if not get_runtime_config()["alerts_enabled"]:
            return
        user_ids = get_all_active_user_ids()
        if not user_ids:
            return

        side_emoji = "🟢" if alert.side == "bid" else "🔴"
        side_label = "покупка (bid)" if alert.side == "bid" else "продажа (ask)"
        text = (
            f"📊 <b>Крупная заявка в стакане</b>\n\n"
            f"{side_emoji} <b>{alert.ticker}</b> — {side_label}\n"
            f"💰 Цена: <b>{alert.price:,.2f} ₽</b>\n"
            f"📦 Объём: <b>{alert.quantity:,} лот.</b> "
            f"({alert.value_rub / 1_000_000:.1f} млн ₽)\n"
            f"📈 Превышает средний размер в <b>{alert.relative_size:.1f}×</b>"
        )

        for uid in user_ids:
            try:
                favs = get_favorites(uid)
                if favs:
                    if not any(f["ticker"] == alert.ticker for f in favs):
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
        time_str = alert.publish_time.strftime("%d.%m.%Y %H:%M") if alert.publish_time else ""
        text = (
            f"🏢 <b>Корпоративное событие</b>\n\n"
            f"{priority_badge}<b>{alert.ticker}</b> — {alert.short_name}\n"
            f"📰 {alert.event_type}\n"
            f"🕐 {time_str}\n"
        )
        if alert.description:
            text += f"\n{alert.description[:300]}\n"
        if alert.source_url:
            text += f"\n🔗 <a href='{alert.source_url}'>Источник</a>"

        for uid in user_ids:
            try:
                favs = get_favorites(uid)
                if favs:
                    if not any(f["ticker"] == alert.ticker for f in favs):
                        continue
                await bot.send_message(uid, text, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as e:
                logger.debug("Corp report send error user %d: %s", uid, e)

    return send_corp_report


async def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        logger.error("❌ Укажите BOT_TOKEN в .env файле!")
        sys.exit(1)

    logger.info("Инициализация базы данных...")
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    keep_alive(bot, asyncio.get_running_loop())  # Render health + web admin API
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Создаём callbacks для рассылки
    send_alerts = await make_alert_sender(bot)
    send_orderbook = await make_orderbook_callback(bot)

    async def notify_admins(message: str) -> None:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"🛠 <b>Состояние сканера</b>\n\n{message}")
            except Exception as exc:
                logger.warning("Не удалось уведомить администратора %s: %s", admin_id, exc)

    # Запускаем основной сканер рынка
    scanner = MarketScanner(
        alert_callback=send_alerts,
        interval_seconds=SCAN_INTERVAL,
        health_callback=notify_admins,
    )
    scanner.start()
    logger.info("Сканер рынка запущен (интервал %d сек)", SCAN_INTERVAL)

    from market.outcome_tracker import OutcomeTracker
    outcome_tracker = OutcomeTracker()
    outcome_tracker.start()

    # Запускаем детектор крупных заявок
    orderbook_watcher = None
    try:
        from market.orderbook_watcher import OrderbookWatcher
        from market.moex import get_all_moex_tickers

        # get_all_moex_tickers — async, возвращает list[str]
        ticker_strs = await get_all_moex_tickers("TQBR")
        # OrderbookWatcher принимает list[tuple[str, str]]: (ticker, short_name)
        ob_tickers = [(t, t) for t in ticker_strs[:100]]
        if ob_tickers:
            orderbook_watcher = OrderbookWatcher(
                tickers=ob_tickers,
                alert_callback=send_orderbook,
            )
            await orderbook_watcher.start()
            logger.info("OrderbookWatcher запущен (%d тикеров)", len(ob_tickers))
    except Exception as e:
        logger.warning("OrderbookWatcher не запущен: %s", e)

    # Новости и корпоративные события отключены по решению владельца.
    corp_watcher = None

    # Утренние/вечерние сводки и напоминания о подписке
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from market.reports import send_morning_report, send_evening_report, send_expiry_reminders
        from database.db import backup_sqlite
        scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        scheduler.add_job(send_morning_report, "cron", day_of_week="mon-fri", hour=9, minute=45, args=[bot])
        scheduler.add_job(send_evening_report, "cron", day_of_week="mon-fri", hour=19, minute=5, args=[bot])
        scheduler.add_job(send_expiry_reminders, "cron", hour=10, minute=15, args=[bot])
        scheduler.add_job(backup_sqlite, "cron", hour=3, minute=20)
        scheduler.start()
        logger.info("APScheduler запущен (сводки и напоминания)")
    except ImportError:
        logger.info("APScheduler не установлен — напоминания о подписке отключены")
    except Exception as e:
        logger.warning("APScheduler не запущен: %s", e)

    logger.info("🚀 Бот запущен!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scanner.stop()
        outcome_tracker.stop()
        if orderbook_watcher:
            await orderbook_watcher.stop()
        if corp_watcher:
            await corp_watcher.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
