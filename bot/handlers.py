"""
Все хэндлеры бота: команды, callback-кнопки, платежи.
"""
import logging
import os
import html
from datetime import timezone

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    LabeledPrice, PreCheckoutQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import (
    SUBSCRIPTION_PRICE_STARS,
    SUBSCRIPTION_TITLE,
    SUBSCRIPTION_DESCRIPTION,
    ADMIN_IDS,
    SUPPORT_USERNAME,
)
from database.db import (
    upsert_user,
    has_active_subscription,
    get_subscription_info,
    had_trial,
    activate_trial,
    activate_paid,
    deactivate_subscription,
    add_user_ticker,
    remove_user_ticker,
    get_user_tickers,
    get_stats,
    get_subscription_price,
    set_subscription_price,
    grant_trial,
    get_all_users,
    add_favorite,
    remove_favorite,
    get_favorites,
    record_payment,
    get_payment_user_id,
    get_user_settings,
    toggle_user_setting,
    get_recent_alerts,
)
from bot.keyboards import (
    main_menu_kb,
    buy_menu_kb,
    back_kb,
    tickers_kb,
    favorites_kb,
    settings_kb,
)

logger = logging.getLogger(__name__)
router = Router()


class AddTickerState(StatesGroup):
    waiting_for_ticker = State()


class AddFavoriteState(StatesGroup):
    waiting_for_ticker = State()


class AdminState(StatesGroup):
    waiting_new_price = State()
    waiting_grant_user_id = State()


# ─── /start ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    name = html.escape(msg.from_user.first_name or "трейдер")
    banner_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "moex-help-banner.png")
    if os.path.exists(banner_path):
        await msg.answer_photo(
            FSInputFile(banner_path),
            caption="<b>MOEX HELP</b>  ·  Умный радар рынка",
            parse_mode="HTML",
        )
    await msg.answer(
        f"<b>MOEX HELP</b>  ·  умный радар рынка\n\n"
        f"Добро пожаловать, <b>{name}</b> 👋\n\n"
        "Я отслеживаю акции Московской биржи и сообщаю только о заметных событиях:\n\n"
        "🔥 необычный торговый объём\n"
        "📈 резкое движение цены\n"
        "⚡ выход за границы волатильности\n\n"
        "Настройте собственную ленту — и получайте только нужные сигналы.",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


# ─── Навигация ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "<b>MOEX HELP</b>  ·  панель управления\n\n"
        "Рынок меняется быстро. Выберите нужный раздел:",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


# ─── Статус подписки ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "about")
async def cb_about(cb: CallbackQuery) -> None:
    price = get_subscription_price()
    await cb.message.edit_text(
        "ℹ️ <b>О боте AnomalyAlert</b>\n\n"
        "Бот каждую минуту сканирует <b>все акции MOEX TQBR</b> (~200+ бумаг):\n\n"
        "🔊 <b>Аномальный объём</b> — объём торгов превышает среднее в 3× и более\n"
        "🚨 <b>Резкое движение</b> — цена изменилась более чем на 3% за минуту\n"
        "📈📉 <b>Полосы Боллинджера</b> — цена вышла за 2.5σ от скользящей средней\n\n"
        f"💰 Стоимость: <b>{price['stars']} ⭐ Stars / месяц</b>\n"
        "🎁 Пробный период: <b>24 часа бесплатно</b>",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data == "settings")
async def cb_settings(cb: CallbackQuery) -> None:
    settings = get_user_settings(cb.from_user.id)
    await cb.message.edit_text(
        "<b>🎛 Настройки сигналов</b>\n\n"
        "Нажмите на пункт, чтобы включить или отключить его.\n"
        "Тихие часы работают по московскому времени.",
        reply_markup=settings_kb(settings),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("setting:"))
async def cb_toggle_setting(cb: CallbackQuery) -> None:
    setting = cb.data.split(":", 1)[1]
    settings = toggle_user_setting(cb.from_user.id, setting)
    await cb.message.edit_reply_markup(reply_markup=settings_kb(settings))
    await cb.answer("Настройка сохранена")


@router.callback_query(F.data == "recent_alerts")
async def cb_recent_alerts(cb: CallbackQuery) -> None:
    alerts = get_recent_alerts(10)
    if not alerts:
        text = (
            "<b>📡 Лента сигналов</b>\n\n"
            "Сигналов пока нет. Лента обновится, когда рынок покажет заметную аномалию."
        )
    else:
        icons = {
            "volume_spike": "🔥",
            "price_move": "📈",
            "bollinger": "⚡",
            "combined": "🚨",
        }
        lines = ["<b>📡 Последние сигналы рынка</b>", ""]
        for alert in alerts:
            icon = icons.get(alert["alert_type"], "•")
            headline = html.escape(str(alert.get("headline") or alert["alert_type"]))
            ticker = html.escape(str(alert["ticker"]))
            lines.append(f"{icon} <b>{ticker}</b> · {headline}")
        text = "\n".join(lines)
    await cb.message.edit_text(text, reply_markup=back_kb(), parse_mode="HTML")
    await cb.answer()




@router.callback_query(F.data == "status")
async def cb_status(cb: CallbackQuery) -> None:
    info = get_subscription_info(cb.from_user.id)
    if info:
        exp = info["expires_at"].strftime("%d.%m.%Y %H:%M МСК")
        sub_type = "🎁 Пробная" if info["type"] == "trial" else "💎 Платная"
        text = (
            f"✅ <b>Подписка активна</b>\n\n"
            f"Тип: {sub_type}\n"
            f"Действует до: <b>{exp}</b>"
        )
    else:
        text = (
            "❌ <b>Подписка не активна</b>\n\n"
            "Оформите подписку, чтобы получать алерты."
        )
    await cb.message.edit_text(
        text,
        reply_markup=buy_menu_kb() if not info else back_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


# ─── Пробный период ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "trial")
async def cb_trial(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    if has_active_subscription(uid):
        await cb.answer("У вас уже есть активная подписка!", show_alert=True)
        return
    if had_trial(uid):
        await cb.answer(
            "Пробный период уже был использован. Оформите платную подписку.",
            show_alert=True,
        )
        return
    if not activate_trial(uid):
        await cb.message.edit_text(
            "⛔ Пробный период уже был использован.\n\nДля продолжения оформите платную подписку.",
            reply_markup=back_kb(),
            parse_mode="HTML",
        )
        await cb.answer()
        return
    await cb.message.edit_text(
        "🎉 <b>Пробный период на 24 часа активирован!</b>\n\n"
        "Вы будете получать алерты об аномалиях на бирже.\n"
        "После окончания пробного периода оформите платную подписку.",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await cb.answer("Пробный период активирован!")


# ─── Покупка подписки ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "buy")
async def cb_buy(cb: CallbackQuery) -> None:
    info = get_subscription_info(cb.from_user.id)
    extra = ""
    if info:
        exp = info["expires_at"].strftime("%d.%m.%Y %H:%M МСК")
        extra = f"\n\n⚠️ У вас уже есть подписка до {exp}. Новый платёж продлит её."
    await cb.message.edit_text(
        f"⭐ <b>Подписка AnomalyAlert</b>\n\n"
        f"💰 Стоимость: <b>{SUBSCRIPTION_PRICE_STARS} Telegram Stars</b> / месяц\n"
        f"🔄 Мониторинг каждые 60 секунд\n"
        f"📊 Топ-15 акций ММВБ + персональные тикеры{extra}",
        reply_markup=buy_menu_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


def _stars_plan(months: int) -> tuple[int, str]:
    monthly = get_subscription_price()["stars"]
    if months == 3:
        return round(monthly * 2.7), "Подписка на 3 месяца"
    if months == 12:
        return monthly * 9, "Подписка на 12 месяцев"
    return monthly, "Подписка на 30 дней"


@router.callback_query(F.data == "pay_stars")
@router.callback_query(F.data.startswith("pay_stars:"))
async def cb_pay_stars(cb: CallbackQuery, bot: Bot) -> None:
    months = 1
    if ":" in cb.data:
        months = int(cb.data.split(":", 1)[1])
    if months not in (1, 3, 12):
        await cb.answer("Неизвестный тариф", show_alert=True)
        return
    price, label = _stars_plan(months)
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=SUBSCRIPTION_TITLE,
        description=SUBSCRIPTION_DESCRIPTION,
        payload=f"sub_{cb.from_user.id}_{months}",
        currency="XTR",   # Telegram Stars
        prices=[LabeledPrice(label=label, amount=price)],
    )
    await cb.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    parts = query.invoice_payload.split("_")
    try:
        payload_user_id = int(parts[1])
        months = int(parts[2])
    except (IndexError, ValueError):
        payload_user_id, months = 0, 0
    expected_price = _stars_plan(months)[0] if months in (1, 3, 12) else -1
    is_valid = (
        query.currency == "XTR"
        and query.total_amount == expected_price
        and payload_user_id == query.from_user.id
        and months in (1, 3, 12)
    )
    await query.answer(
        ok=is_valid,
        error_message=None if is_valid else "Параметры платежа устарели. Откройте меню оплаты заново.",
    )


@router.message(F.successful_payment)
async def payment_success(msg: Message) -> None:
    p = msg.successful_payment
    try:
        months = int(p.invoice_payload.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        months = 1
    if months not in (1, 3, 12):
        logger.error("Некорректный тариф в successful_payment: %s", p.invoice_payload)
        return
    charge_id = p.telegram_payment_charge_id
    is_new = record_payment(msg.from_user.id, charge_id, p.total_amount, p.currency)
    if is_new:
        activate_paid(msg.from_user.id, months)
    else:
        logger.warning("Повторный successful_payment проигнорирован: charge_id=%s", charge_id)
    await msg.answer(
        "✅ <b>Оплата прошла успешно!</b>\n\n"
        f"Вы оплатили <b>{p.total_amount} ⭐ Stars</b>.\n"
        f"Подписка активна на <b>{months} мес.</b> Алерты уже идут!\n\n"
        "Настройте персональные тикеры через меню «📋 Мои тикеры».",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
    logger.info("Оплата от user_id=%d, charge_id=%s, stars=%d",
                msg.from_user.id, charge_id, p.total_amount)


@router.message(F.refunded_payment)
async def payment_refund(msg: Message) -> None:
    p = msg.refunded_payment
    charge_id = p.telegram_payment_charge_id
    user_id = get_payment_user_id(charge_id)
    if user_id is None:
        logger.error("Не найден пользователь для возврата Stars: charge_id=%s", charge_id)
        await msg.answer("↩️ Возврат получен, но подписка требует ручной проверки. Напишите в поддержку.")
        return
    deactivate_subscription(user_id)
    await msg.answer(
        "↩️ Возврат Stars выполнен. Ваша подписка деактивирована.",
        reply_markup=main_menu_kb(),
    )


# ─── Персональные тикеры ──────────────────────────────────────────────────────

@router.callback_query(F.data == "my_tickers")
async def cb_my_tickers(cb: CallbackQuery) -> None:
    if not has_active_subscription(cb.from_user.id):
        await cb.answer(
            "Персональные тикеры доступны только с активной подпиской.",
            show_alert=True,
        )
        return
    tickers = get_user_tickers(cb.from_user.id)
    if tickers:
        text = "📋 <b>Ваши тикеры</b>\nНажмите на тикер, чтобы удалить его:"
    else:
        text = "📋 <b>Ваши тикеры</b>\nСписок пуст. Добавьте тикеры для отслеживания:"
    await cb.message.edit_text(
        text,
        reply_markup=tickers_kb(tickers),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data == "add_ticker")
async def cb_add_ticker(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddTickerState.waiting_for_ticker)
    await cb.message.edit_text(
        "✏️ Введите тикер в формате <b>TICKER:EXCHANGE</b>\n\n"
        "Примеры:\n"
        "• <code>SBER:MOEX</code>\n"
        "• <code>AAPL:SPBEX</code>\n\n"
        "Или просто тикер (например <code>LKOH</code>) — по умолчанию MOEX.",
        reply_markup=back_kb("my_tickers"),
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(AddTickerState.waiting_for_ticker)
async def handle_ticker_input(msg: Message, state: FSMContext) -> None:
    await state.clear()
    raw = msg.text.strip().upper()
    if ":" in raw:
        ticker, exchange = raw.split(":", 1)
    else:
        ticker, exchange = raw, "MOEX"

    if not ticker.isalpha():
        await msg.answer("❌ Неверный формат тикера.", reply_markup=main_menu_kb())
        return

    exchange = exchange if exchange in ("MOEX", "SPBEX") else "MOEX"
    added = add_user_ticker(msg.from_user.id, ticker, exchange)
    if added:
        await msg.answer(
            f"✅ Тикер <b>{ticker} ({exchange})</b> добавлен в отслеживание.",
            reply_markup=main_menu_kb(),
            parse_mode="HTML",
        )
    else:
        await msg.answer("⚠️ Тикер уже в списке или произошла ошибка.",
                         reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("del_ticker:"))
async def cb_del_ticker(cb: CallbackQuery) -> None:
    _, ticker, exchange = cb.data.split(":", 2)
    remove_user_ticker(cb.from_user.id, ticker, exchange)
    tickers = get_user_tickers(cb.from_user.id)
    if tickers:
        text = "📋 <b>Ваши тикеры</b>\nНажмите на тикер, чтобы удалить его:"
    else:
        text = "📋 <b>Ваши тикеры</b>\nСписок пуст."
    await cb.message.edit_text(
        text,
        reply_markup=tickers_kb(tickers),
        parse_mode="HTML",
    )
    await cb.answer(f"Тикер {ticker} удалён")


# ─── Избранные акции ──────────────────────────────────────────────────────────

def _favs_text(favs: list[dict]) -> str:
    if favs:
        return (
            "🌟 <b>Избранные акции</b>\n\n"
            "Алерты будут приходить <b>только по этим акциям</b>.\n"
            "Нажмите на акцию, чтобы удалить её из избранного:"
        )
    return (
        "🌟 <b>Избранные акции</b>\n\n"
        "Список пуст. Пока избранное пустое — вы получаете "
        "<b>все алерты по рынку</b>.\n\n"
        "Добавьте акции, чтобы получать алерты только по ним."
    )


@router.callback_query(F.data == "favorites")
async def cb_favorites(cb: CallbackQuery) -> None:
    if not has_active_subscription(cb.from_user.id):
        await cb.answer(
            "Избранное доступно только с активной подпиской.",
            show_alert=True,
        )
        return
    favs = get_favorites(cb.from_user.id)
    await cb.message.edit_text(
        _favs_text(favs),
        reply_markup=favorites_kb(favs),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data == "add_favorite")
async def cb_add_favorite(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddFavoriteState.waiting_for_ticker)
    await cb.message.edit_text(
        "✏️ <b>Добавить акцию в избранное</b>\n\n"
        "Введите тикер акции (только MOEX):\n\n"
        "Примеры: <code>SBER</code>, <code>LKOH</code>, <code>GAZP</code>",
        reply_markup=back_kb("favorites"),
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(AddFavoriteState.waiting_for_ticker)
async def handle_favorite_input(msg: Message, state: FSMContext) -> None:
    await state.clear()
    ticker = msg.text.strip().upper()
    # Убираем лишнее (если ввели SBER:MOEX — берём только тикер)
    if ":" in ticker:
        ticker = ticker.split(":")[0]

    if not ticker.isalpha() or len(ticker) > 10:
        await msg.answer(
            "❌ Неверный формат. Введите тикер буквами, например: <code>SBER</code>",
            reply_markup=main_menu_kb(),
            parse_mode="HTML",
        )
        return

    added = add_favorite(msg.from_user.id, ticker, "MOEX")
    if added:
        await msg.answer(
            f"✅ <b>{ticker}</b> добавлен в избранное!\n\n"
            "Теперь вы будете получать алерты только по акциям из избранного.",
            reply_markup=main_menu_kb(),
            parse_mode="HTML",
        )
    else:
        await msg.answer(
            "⚠️ Акция уже в избранном.",
            reply_markup=main_menu_kb(),
        )


@router.callback_query(F.data.startswith("del_fav:"))
async def cb_del_favorite(cb: CallbackQuery) -> None:
    parts = cb.data.split(":", 2)
    ticker = parts[1]
    exchange = parts[2] if len(parts) > 2 else "MOEX"
    remove_favorite(cb.from_user.id, ticker, exchange)
    favs = get_favorites(cb.from_user.id)
    await cb.message.edit_text(
        _favs_text(favs),
        reply_markup=favorites_kb(favs),
        parse_mode="HTML",
    )
    await cb.answer(f"{ticker} удалён из избранного")


# ─── Поддержка ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "support")
async def cb_support(cb: CallbackQuery) -> None:
    if SUPPORT_USERNAME:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✉️ Написать в поддержку",
                url=f"https://t.me/{SUPPORT_USERNAME}",
            )],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
        ])
        text = (
            "💬 <b>Поддержка</b>\n\n"
            "Если у вас возникли вопросы или проблемы — напишите нам!\n\n"
            "Мы отвечаем в течение 24 часов."
        )
    else:
        kb = back_kb()
        text = "💬 <b>Поддержка</b>\n\nПоддержка временно недоступна. Попробуйте позже."
    await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await cb.answer()


# ─── Команды администратора ───────────────────────────────────────────────────

def _admin_panel_text() -> str:
    s = get_stats()
    price = get_subscription_price()
    return (
        "🛠 <b>Панель администратора</b>\n\n"
        f"👤 Всего пользователей: <b>{s['total_users']}</b>\n"
        f"✅ Активных подписок: <b>{s['active_subs']}</b>\n"
        f"💎 Платных подписок: <b>{s['paid_subs']}</b>\n"
        f"🔔 Алертов за 24ч: <b>{s['alerts_today']}</b>\n\n"
        f"💰 Текущая цена: <b>{price} ⭐ Stars / мес</b>"
    )


def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Изменить цену подписки", callback_data="admin_set_price")],
        [InlineKeyboardButton(text="🎁 Выдать trial пользователю", callback_data="admin_grant_trial")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
    ])


@router.message(Command("admin"))
async def cmd_admin(msg: Message) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    await msg.answer(
        _admin_panel_text(),
        reply_markup=_admin_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.edit_text(
        _admin_panel_text(),
        reply_markup=_admin_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


# ── Изменить цену подписки ────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_set_price")
async def cb_admin_set_price(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    current = get_subscription_price()
    await state.set_state(AdminState.waiting_new_price)
    await cb.message.edit_text(
        f"💰 Текущая цена: <b>{current} ⭐ Stars</b>\n\n"
        "Введите новую цену подписки в Stars (целое число, минимум 1):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(AdminState.waiting_new_price)
async def handle_new_price(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    try:
        new_price = int(msg.text.strip())
        assert new_price >= 1
    except (ValueError, AssertionError):
        await msg.answer("❌ Неверное значение. Введите целое положительное число.")
        return
    set_subscription_price(new_price)
    await msg.answer(
        f"✅ Цена подписки обновлена: <b>{new_price} ⭐ Stars / месяц</b>",
        reply_markup=_admin_kb(),
        parse_mode="HTML",
    )


# ── Выдать trial вручную ──────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_grant_trial")
async def cb_admin_grant_trial(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminState.waiting_grant_user_id)
    await cb.message.edit_text(
        "🎁 <b>Выдать пробный период</b>\n\n"
        "Введите <b>user_id</b> пользователя (можно узнать командой /id у пользователя).\n\n"
        "Формат: <code>123456789</code>\n"
        "Или с указанием часов: <code>123456789 48</code> (48 часов)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(AdminState.waiting_grant_user_id)
async def handle_grant_user_id(msg: Message, state: FSMContext, bot: Bot) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    parts = msg.text.strip().split()
    try:
        target_id = int(parts[0])
        hours = int(parts[1]) if len(parts) > 1 else 24
        assert hours >= 1
    except (ValueError, IndexError, AssertionError):
        await msg.answer(
            "❌ Неверный формат. Пример: <code>123456789</code> или <code>123456789 48</code>",
            parse_mode="HTML",
        )
        return

    grant_trial(target_id, hours=hours)

    # Уведомляем пользователя
    try:
        await bot.send_message(
            target_id,
            f"🎁 <b>Вам выдан пробный период на {hours} ч!</b>\n\n"
            "Теперь вы будете получать алерты об аномалиях на бирже.",
            parse_mode="HTML",
        )
        notified = "✉️ Пользователь уведомлён."
    except Exception:
        notified = "⚠️ Не удалось уведомить пользователя (возможно, заблокировал бота)."

    await msg.answer(
        f"✅ Пробный период на <b>{hours} ч</b> выдан пользователю <code>{target_id}</code>.\n{notified}",
        reply_markup=_admin_kb(),
        parse_mode="HTML",
    )


# ── Список пользователей ──────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_users")
async def cb_admin_users(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    users = get_all_users()
    if not users:
        text = "👥 Пользователей пока нет."
    else:
        lines = ["👥 <b>Последние пользователи</b>\n"]
        for u in users[:30]:
            name = u.get("first_name") or u.get("username") or "—"
            uid = u["user_id"]
            sub = u.get("sub_type")
            if sub:
                exp = u["expires_at"].strftime("%d.%m") if u["expires_at"] else "?"
                sub_str = f"{'🎁' if sub == 'trial' else '💎'} до {exp}"
            else:
                sub_str = "❌ нет"
            lines.append(f"<code>{uid}</code> {name} — {sub_str}")
        text = "\n".join(lines)
    await cb.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
        ]),
        parse_mode="HTML",
    )
    await cb.answer()


# ── Рассылка ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.edit_text(
        "📢 <b>Рассылка</b>\n\nОтправьте текст сообщения (HTML разрешён):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="HTML",
    )
    await state.set_state(AdminState.waiting_grant_user_id)  # reuse state for text
    await cb.answer()


@router.message(Command("broadcast"))
async def cmd_broadcast(msg: Message, bot: Bot) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    text = msg.text.removeprefix("/broadcast").strip()
    if not text:
        await msg.answer("Использование: /broadcast <текст>")
        return
    from database.db import get_all_active_user_ids
    user_ids = get_all_active_user_ids()
    sent = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except Exception:
            pass
    await msg.answer(f"✅ Рассылка отправлена {sent} пользователям.")


# ── /id — узнать свой user_id ─────────────────────────────────────────────────

@router.message(Command("id"))
async def cmd_id(msg: Message) -> None:
    await msg.answer(f"Ваш Telegram ID: <code>{msg.from_user.id}</code>", parse_mode="HTML")


# ─── ЮKassa: оплата картой / СБП ─────────────────────────────────────────────

def _yukassa_plans_kb() -> InlineKeyboardMarkup:
    """Клавиатура выбора тарифа ЮKassa."""
    try:
        from bot.payments_yukassa import PLANS
        buttons = []
        for key, plan in PLANS.items():
            buttons.append([InlineKeyboardButton(
                text=f"💳 {plan['label']} — {plan['price']} ₽",
                callback_data=f"yk_plan:{key}",
            )])
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="buy")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    except ImportError:
        return back_kb("buy")


@router.callback_query(F.data == "pay_yukassa")
async def cb_pay_yukassa(cb: CallbackQuery) -> None:
    """Показывает тарифы ЮKassa."""
    try:
        from bot.payments_yukassa import YUKASSA_AVAILABLE
        if not YUKASSA_AVAILABLE:
            await cb.answer(
                "Оплата картой временно недоступна. Используйте Telegram Stars.",
                show_alert=True,
            )
            return
    except ImportError:
        await cb.answer("Оплата картой недоступна.", show_alert=True)
        return

    await cb.message.edit_text(
        "💳 <b>Оплата картой / СБП</b>\n\n"
        "Выберите тариф:\n\n"
        "✅ Оплата через банковскую карту или СБП\n"
        "🔒 Безопасная оплата через ЮKassa",
        reply_markup=_yukassa_plans_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("yk_plan:"))
async def cb_yukassa_plan(cb: CallbackQuery) -> None:
    """Создаёт платёж ЮKassa для выбранного тарифа."""
    plan_key = cb.data.split(":", 1)[1]
    try:
        from bot.payments_yukassa import PLANS, create_payment
    except ImportError:
        await cb.answer("Оплата недоступна.", show_alert=True)
        return

    plan = PLANS.get(plan_key)
    if not plan:
        await cb.answer("Неизвестный тариф.", show_alert=True)
        return

    await cb.answer("Создаём ссылку на оплату...", show_alert=False)

    # return_url — ссылка на бота (пользователь вернётся сюда после оплаты)
    return_url = f"https://t.me/{(await cb.bot.get_me()).username}"
    result = await create_payment(cb.from_user.id, plan_key, return_url)

    if not result:
        await cb.message.edit_text(
            "❌ Не удалось создать платёж. Попробуйте позже или оплатите через Telegram Stars.",
            reply_markup=back_kb("buy"),
        )
        return

    pay_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💳 Оплатить {plan['price']} ₽",
            url=result["confirmation_url"],
        )],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="buy")],
    ])

    await cb.message.edit_text(
        f"💳 <b>Оплата через ЮKassa</b>\n\n"
        f"Тариф: <b>{plan['label']}</b>\n"
        f"Сумма: <b>{plan['price']} ₽</b>\n\n"
        "Нажмите кнопку ниже для перехода к оплате.\n"
        "После оплаты подписка активируется автоматически.",
        reply_markup=pay_kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "trial_yukassa")
async def cb_trial_yukassa(cb: CallbackQuery) -> None:
    """Активирует пробный период (ЮKassa вариант)."""
    try:
        from bot.payments_yukassa import activate_trial, is_subscribed, TRIAL_DAYS
    except ImportError:
        await cb.answer("Пробный период недоступен.", show_alert=True)
        return

    uid = cb.from_user.id
    if is_subscribed(uid):
        await cb.answer("У вас уже есть активная подписка!", show_alert=True)
        return

    ok = activate_trial(uid)
    if ok:
        await cb.message.edit_text(
            f"🎉 <b>Пробный период на {TRIAL_DAYS} дней активирован!</b>\n\n"
            "Вы будете получать все алерты об аномалиях на бирже.\n"
            "После окончания периода оформите подписку командой /subscribe.",
            reply_markup=back_kb(),
            parse_mode="HTML",
        )
        await cb.answer("Пробный период активирован!")
    else:
        await cb.answer(
            "Пробный период уже был использован. Оформите платную подписку.",
            show_alert=True,
        )


@router.message(Command("subscribe"))
async def cmd_subscribe(msg: Message) -> None:
    """Показывает меню подписки."""
    try:
        from bot.payments_yukassa import get_subscription, TRIAL_DAYS, YUKASSA_AVAILABLE
        sub = get_subscription(msg.from_user.id)
        yukassa_btn = YUKASSA_AVAILABLE
    except ImportError:
        sub = None
        yukassa_btn = False

    if sub and sub["is_active"]:
        exp = sub["expires_at"].strftime("%d.%m.%Y")
        plan = sub.get("plan", "")
        trial_note = " (пробный)" if sub.get("is_trial") else ""
        text = (
            f"✅ <b>Подписка активна{trial_note}</b>\n\n"
            f"Тариф: {plan}\n"
            f"Действует до: <b>{exp}</b> ({sub['days_left']} дн.)\n\n"
            "Для продления используйте кнопку ниже."
        )
    else:
        text = (
            "📱 <b>Подписка AnomalyAlert</b>\n\n"
            "Получайте алерты об аномалиях на Московской бирже.\n\n"
            "Выберите способ оплаты:"
        )

    buttons = []
    if yukassa_btn:
        buttons.append([InlineKeyboardButton(text="💳 Оплатить картой / СБП", callback_data="pay_yukassa")])
    buttons.append([InlineKeyboardButton(text="⭐ Оплатить Telegram Stars", callback_data="pay_stars")])
    if not sub:
        buttons.append([InlineKeyboardButton(
            text=f"🎁 Пробный период ({TRIAL_DAYS} дней)",
            callback_data="trial_yukassa" if yukassa_btn else "trial",
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")])

    await msg.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
