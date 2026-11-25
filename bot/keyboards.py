"""
Клавиатуры и кнопки Telegram.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import SUBSCRIPTION_PRICE_STARS


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 Лента сигналов", callback_data="recent_alerts"),
         InlineKeyboardButton(text="⭐ Избранное", callback_data="favorites")],
        [InlineKeyboardButton(text="📈 Качество сигналов", callback_data="signal_stats")],
        [InlineKeyboardButton(text="🎛 Настроить алерты", callback_data="settings")],
        [InlineKeyboardButton(text="💎 Подписка", callback_data="status"),
         InlineKeyboardButton(text="🚀 Купить", callback_data="buy")],
        [InlineKeyboardButton(text="🧾 История платежей", callback_data="payment_history")],
        [InlineKeyboardButton(text="🎁 Попробовать 24 часа", callback_data="trial")],
        [InlineKeyboardButton(text="🤝 Пригласить друга", callback_data="referral")],
        [InlineKeyboardButton(text="ℹ️ Возможности", callback_data="about"),
         InlineKeyboardButton(text="💬 Поддержка", callback_data="support")],
    ])


def settings_kb(settings: dict) -> InlineKeyboardMarkup:
    mark = lambda enabled: "✅" if enabled else "⚪"
    pause_text = "▶️ Возобновить уведомления" if settings["paused"] else "⏸ Приостановить уведомления"
    direction_label = {"all": "↕️ Рост и падение", "up": "📈 Только рост", "down": "📉 Только падение"}[settings["direction"]]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{mark(settings['volume_alerts'])} Аномальный объём",
            callback_data="setting:volume_alerts",
        )],
        [InlineKeyboardButton(
            text=f"{mark(settings['price_alerts'])} Резкое движение",
            callback_data="setting:price_alerts",
        )],
        [InlineKeyboardButton(
            text=f"{mark(settings['band_alerts'])} Полосы Боллинджера",
            callback_data="setting:band_alerts",
        )],
        [InlineKeyboardButton(
            text=f"{mark(settings['quiet_hours'])} Тихие часы 23:00–07:00",
            callback_data="setting:quiet_hours",
        )],
        [InlineKeyboardButton(text=f"🎯 Мин. рейтинг: {settings['min_score']}/100", callback_data="preference:min_score")],
        [InlineKeyboardButton(text=direction_label, callback_data="preference:direction")],
        [InlineKeyboardButton(text=pause_text, callback_data="setting:paused")],
        [InlineKeyboardButton(text="‹ Главное меню", callback_data="main_menu")],
    ])


def buy_menu_kb() -> InlineKeyboardMarkup:
    try:
        from bot.payments_yukassa import YUKASSA_AVAILABLE
        show_yukassa = YUKASSA_AVAILABLE
    except ImportError:
        show_yukassa = False

    buttons = []
    if show_yukassa:
        buttons.append([InlineKeyboardButton(
            text="💳 Оплатить картой / СБП (ЮKassa)",
            callback_data="pay_yukassa",
        )])
    buttons.extend([
        [InlineKeyboardButton(
            text=f"⭐ 1 месяц · {SUBSCRIPTION_PRICE_STARS} Stars",
            callback_data="pay_stars:1",
        )],
        [InlineKeyboardButton(
            text=f"⭐ 3 месяца · {round(SUBSCRIPTION_PRICE_STARS * 2.7)} Stars · −10%",
            callback_data="pay_stars:3",
        )],
        [InlineKeyboardButton(
            text=f"⭐ 12 месяцев · {SUBSCRIPTION_PRICE_STARS * 9} Stars · −25%",
            callback_data="pay_stars:12",
        )],
        [InlineKeyboardButton(text="🎁 Подарить подписку", callback_data="gift_subscription")],
        [InlineKeyboardButton(text="🏷 Ввести промокод", callback_data="promo_code")],
    ])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_kb(cb: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data=cb)]
    ])


def favorites_kb(favs: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for f in favs:
        label = f"❌ {f['ticker']} ({f['exchange']})"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"del_fav:{f['ticker']}:{f['exchange']}",
        )])
    rows.append([InlineKeyboardButton(text="➕ Добавить акцию", callback_data="add_favorite")])
    rows.append([InlineKeyboardButton(text="🔙 Назад",           callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tickers_kb(tickers: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for t in tickers:
        label = f"❌ {t['ticker']} ({t['exchange']})"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"del_ticker:{t['ticker']}:{t['exchange']}",
        )])
    rows.append([InlineKeyboardButton(text="➕ Добавить тикер", callback_data="add_ticker")])
    rows.append([InlineKeyboardButton(text="🔙 Назад",           callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
