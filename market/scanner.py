"""
Фоновый сканер рынка: периодически опрашивает MOEX ISS,
детектирует аномалии и рассылает алерты подписчикам.
"""
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot

from config import POLL_INTERVAL_SEC, HISTORY_BARS
from market.moex import get_moex_securities, get_moex_candles
from market.anomaly import run_all_detectors, Alert
from database.db import (
    get_all_active_user_ids,
    get_user_tickers,
    log_alert,
)

logger = logging.getLogger(__name__)

DEFAULT_MOEX_TICKERS = [
    "SBER", "GAZP", "LKOH", "NVTK", "MGNT",
    "YNDX", "MTSS", "ROSN", "TATN", "GMKN",
    "VTBR", "AFLT", "CHMF", "NLMK", "ALRS",
]


def _format_alert(alert: Alert) -> str:
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    return (
        f"{alert.emoji} <b>{alert.headline}</b>\n"
        f"⏰ {ts}\n"
        f"ℹ️ {alert.details}"
    )


async def _send_alert_to_users(bot: Bot, alert: Alert) -> None:
    """Рассылает алерт всем активным подписчикам."""
    user_ids = get_all_active_user_ids()
    text = _format_alert(alert)
    log_alert(alert.ticker, alert.exchange, alert.alert_type, alert.details)

    for uid in user_ids:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            await asyncio.sleep(0.05)
        except Exception as exc:
            logger.debug("Не удалось отправить алерт пользователю %d: %s", uid, exc)


async def _scan_ticker(bot: Bot, ticker: str, exchange: str = "MOEX") -> None:
    candles = await get_moex_candles(ticker, n_bars=HISTORY_BARS + 2)
    if not candles:
        return
    alerts = run_all_detectors(ticker, exchange, candles)
    for alert in alerts:
        await _send_alert_to_users(bot, alert)


async def market_scanner_loop(bot: Bot) -> None:
    """Основной цикл сканирования. Запускается как asyncio.Task."""
    logger.info("Сканер рынка запущен (интервал %d сек)", POLL_INTERVAL_SEC)
    while True:
        try:
            for ticker in DEFAULT_MOEX_TICKERS:
                await _scan_ticker(bot, ticker, "MOEX")
                await asyncio.sleep(0.3)

            user_ids = get_all_active_user_ids()
            seen: set[tuple] = set()
            for uid in user_ids:
                for t in get_user_tickers(uid):
                    key = (t["ticker"], t["exchange"])
                    if key not in seen:
                        seen.add(key)
                        await _scan_ticker(bot, t["ticker"], t["exchange"])
                        await asyncio.sleep(0.3)

        except Exception as exc:
            logger.exception("Ошибка в цикле сканера: %s", exc)

        await asyncio.sleep(POLL_INTERVAL_SEC)
