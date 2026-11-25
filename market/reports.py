"""Scheduled user summaries and subscription reminders."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from database.db import (
    get_all_active_user_ids,
    get_expiring_subscriptions,
    get_recent_alerts,
    get_signal_performance,
    get_runtime_config,
)

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))


async def send_morning_report(bot) -> None:
    if not get_runtime_config()["morning_reports"]:
        return
    text = (
        "☀️ <b>Утренний радар MOEX</b>\n\n"
        "Сканер готов к торговому дню. В алертах указываются рейтинг, "
        "причина сигнала и график.\n\n"
        "Проверьте свой минимальный рейтинг в разделе «Настроить алерты»."
    )
    await _broadcast(bot, text)


async def send_evening_report(bot) -> None:
    if not get_runtime_config()["evening_reports"]:
        return
    alerts = get_recent_alerts(5)
    stats = get_signal_performance()
    lines = ["🌆 <b>Итоги торгового дня</b>", ""]
    if alerts:
        lines.append("Последние сигналы:")
        lines.extend(f"• <b>{item['ticker']}</b> — {item.get('headline') or item['alert_type']}" for item in alerts)
    else:
        lines.append("Сегодня сканер не зафиксировал сильных аномалий.")
    if stats["evaluated"]:
        lines.append(f"\nТочность направления: <b>{stats['confirmation_rate']}%</b>")
    lines.append("\nСтатистика не является инвестиционной рекомендацией.")
    await _broadcast(bot, "\n".join(lines))


async def send_expiry_reminders(bot) -> None:
    for min_hours, max_hours, label in ((48, 72, "3 дня"), (18, 30, "24 часа")):
        for row in get_expiring_subscriptions(min_hours, max_hours):
            try:
                expires = datetime.fromtimestamp(float(row["expires_at"]), tz=MSK).strftime("%d.%m.%Y %H:%M")
                await bot.send_message(
                    row["user_id"],
                    f"⏳ <b>Подписка закончится примерно через {label}</b>\n\nДействует до {expires} МСК.",
                )
            except Exception as exc:
                logger.debug("Expiry reminder failed: %s", exc)


async def _broadcast(bot, text: str) -> None:
    for user_id in get_all_active_user_ids():
        try:
            await bot.send_message(user_id, text)
        except Exception as exc:
            logger.debug("Scheduled report failed for %s: %s", user_id, exc)
