"""
Клавиатуры и кнопки Telegram.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import SUBSCRIPTION_PRICE_STARS


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мой статус подписки", callback_data="status")],
        [InlineKeyboardButton(text="Купить подписку", callback_data="buy")],
        [InlineKeyboardButton(text="Пробный период 24ч", callback_data="trial")],
        [InlineKeyboardButton(text="Мои тикеры",        callback_data="my_tickers")],
        [InlineKeyboardButton(text="О боте",             callback_data="about")],
    ])


def buy_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Оплатить {SUBSCRIPTION_PRICE_STARS} Stars / месяц",
            callback_data="pay_stars",
        )],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
    ])


def back_kb(cb: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data=cb)]
    ])


def tickers_kb(tickers: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for t in tickers:
        label = f"X {t['ticker']} ({t['exchange']})"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"del_ticker:{t['ticker']}:{t['exchange']}",
        )])
    rows.append([InlineKeyboardButton(text="Добавить тикер", callback_data="add_ticker")])
    rows.append([InlineKeyboardButton(text="Назад",           callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
