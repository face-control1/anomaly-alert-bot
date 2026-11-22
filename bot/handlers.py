"""
Все хэндлеры бота: команды, callback-кнопки, платежи.
"""
import logging
from datetime import timezone

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    LabeledPrice, PreCheckoutQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
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
)
from bot.keyboards import main_menu_kb, buy_menu_kb, back_kb, tickers_kb, favorites_kb

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
    name = msg.from_user.first_name or "трейдер"
    await msg.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        "Я <b>AnomalyAlert</b> — бот для отслеживания аномальных событий "
        "на <b>Московской бирже</b> и <b>СПБ Бирже</b>.\n\n"
        "🔔 Я мониторю:\n"
        "• Аномальные объёмы торгов\n"
        "• Резкие движения цены (>3%)\n"
        "• Пробои полос Боллинджера\n\n"
        "Выбери действие:",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


# ─── Навигация ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "Главное меню — выбери действие:",
        reply_markup=main_menu_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "about")
async def cb_about(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "ℹ️ <b>О боте AnomalyAlert</b>\n\n"
        "Бот каждую минуту сканирует рынок и присылает алерты:\n\n"
        "🔊 <b>Аномальный объём</b> — объём торгов превышает среднее в 3× и более\n"
        "🚨 <b>Резкое движение</b> — цена изменилась более чем на 3% за минуту\n"
        "📈📉 <b>Полосы Боллинджера</b> — цена вышла за 2.5σ от скользящей средней\n\n"
        "Мониторинг: топ-15 акций ММВБ + ваши персональные тикеры.\n\n"
        "💰 Стоимость: <b>250 ⭐ Stars / месяц</b>\n"
        "🎁 Пробный период: <b>24 часа бесплатно</b>",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


# ─── Статус подписки ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "status")
async def cb_status(cb: CallbackQuery) -> None:
    info = get_subscription_info(cb.from_user.id)
    if info:
        exp = info["expires_at"].strftime("%d.%m.%Y %H:%M UTC")
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
    activate_trial(uid)
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
        exp = info["expires_at"].strftime("%d.%m.%Y %H:%M UTC")
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


@router.callback_query(F.data == "pay_stars")
async def cb_pay_stars(cb: CallbackQuery, bot: Bot) -> None:
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=SUBSCRIPTION_TITLE,
        description=SUBSCRIPTION_DESCRIPTION,
        payload=f"sub_{cb.from_user.id}",
        currency="XTR",   # Telegram Stars
        prices=[LabeledPrice(label="Подписка 30 дней", amount=SUBSCRIPTION_PRICE_STARS)],
    )
    await cb.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def payment_success(msg: Message) -> None:
    p = msg.successful_payment
    activate_paid(msg.from_user.id, p.provider_payment_charge_id, p.total_amount)
    await msg.answer(
        "✅ <b>Оплата прошла успешно!</b>\n\n"
        f"Вы оплатили <b>{p.total_amount} ⭐ Stars</b>.\n"
        "Подписка активна на 30 дней. Алерты уже идут!\n\n"
        "Настройте персональные тикеры через меню «📋 Мои тикеры».",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
    logger.info("Оплата от user_id=%d, charge_id=%s, stars=%d",
                msg.from_user.id, p.provider_payment_charge_id, p.total_amount)


@router.message(F.refunded_payment)
async def payment_refund(msg: Message) -> None:
    p = msg.refunded_payment
    deactivate_subscription(p.provider_payment_charge_id)
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
            f"✅ Тикер <b>{ticker}:{exchange}</b> добавлен!",
            reply_markup=main_menu_kb(),
            parse_mode="HTML",
        )
    else:
        await msg.answer("⚠️ Тикер уже в списке.", reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("del_ticker:"))
async def cb_del_ticker(cb: CallbackQuery) -> None:
    parts = cb.data.split(":", 2)
    ticker = parts[1]
    exchange = parts[2] if len(parts) > 2 else "MOEX"
    remove_user_ticker(cb.from_user.id, ticker, exchange)
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
    await cb.answer(f"{ticker}:{exchange} удалён")


# ─── Избранные акции ───────────────────────────────────────────────────────────

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
        await cb.answer("Избранное доступно только с активной подпиской.", show_alert=True)
        return
    favs = get_favorites(cb.from_user.id)
    await cb.message.edit_text(_favs_text(favs), reply_markup=favorites_kb(favs), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "add_favorite")
async def cb_add_favorite(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddFavoriteState.waiting_for_ticker)
    await cb.message.edit_text(
        "✏️ <b>Добавить акцию в избранное</b>\n\nВведите тикер акции (только MOEX):\n\nПримеры: <code>SBER</code>, <code>LKOH</code>, <code>GAZP</code>",
        reply_markup=back_kb("favorites"), parse_mode="HTML")
    await cb.answer()


@router.message(AddFavoriteState.waiting_for_ticker)
async def handle_favorite_input(msg: Message, state: FSMContext) -> None:
    await state.clear()
    ticker = msg.text.strip().upper()
    if ":" in ticker:
        ticker = ticker.split(":")[0]
    if not ticker.isalpha() or len(ticker) > 10:
        await msg.answer("❌ Неверный формат. Введите тикер буквами, например: <code>SBER</code>",
            reply_markup=main_menu_kb(), parse_mode="HTML")
        return
    added = add_favorite(msg.from_user.id, ticker, "MOEX")
    if added:
        await msg.answer(f"✅ <b>{ticker}</b> добавлен в избранное!\n\nТеперь вы будете получать алерты только по акциям из избранного.",
            reply_markup=main_menu_kb(), parse_mode="HTML")
    else:
        await msg.answer("⚠️ Акция уже в избранном.", reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("del_fav:"))
async def cb_del_favorite(cb: CallbackQuery) -> None:
    parts = cb.data.split(":", 2)
    ticker = parts[1]
    exchange = parts[2] if len(parts) > 2 else "MOEX"
    remove_favorite(cb.from_user.id, ticker, exchange)
    favs = get_favorites(cb.from_user.id)
    await cb.message.edit_text(_favs_text(favs), reply_markup=favorites_kb(favs), parse_mode="HTML")
    await cb.answer(f"{ticker} удалён из избранного")


@router.callback_query(F.data == "support")
async def cb_support(cb: CallbackQuery) -> None:
    if SUPPORT_USERNAME:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Написать в поддержку", url=f"https://t.me/{SUPPORT_USERNAME}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
        ])
        text = "💬 <b>Поддержка</b>\n\nЕсли у вас возникли вопросы или проблемы — напишите нам!\n\nМы отвечаем в течение 24 часов."
    else:
        kb = back_kb()
        text = "💬 <b>Поддержка</b>\n\nПоддержка временно недоступна. Попробуйте позже."
    await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await cb.answer()


# ─── Команды администратора ───────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(msg: Message) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    stats = get_stats()
    price = get_subscription_price()
    await msg.answer(
        "🛡️ <b>Панель администратора</b>\n\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"✅ Активных подписок: {stats['active_subscriptions']}\n"
        f"🎁 Пробных: {stats['trial_subscriptions']}\n"
        f"💰 Цена подписки: <b>{price} Stars</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Изменить цену", callback_data="admin_set_price")],
            [InlineKeyboardButton(text="🎁 Выдать trial",   callback_data="admin_grant_trial")],
            [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_list_users")],
        ]),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_set_price")
async def cb_admin_set_price(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminState.waiting_new_price)
    await cb.message.edit_text(
        "💰 Введите новую цену подписки (в Telegram Stars):",
        reply_markup=back_kb(),
    )
    await cb.answer()


@router.message(AdminState.waiting_new_price)
async def handle_new_price(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    try:
        price = int(msg.text.strip())
        if price <= 0:
            raise ValueError
    except ValueError:
        await msg.answer("❌ Неверный формат. Введите целое положительное число.")
        return
    set_subscription_price(price)
    await msg.answer(f"✅ Цена обновлена: <b>{price} Stars</b>", parse_mode="HTML")

@router.callback_query(F.data == "admin_grant_trial")
async def cb_admin_grant_trial(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminState.waiting_grant_user_id)
    await cb.message.edit_text(
        "🎁 Введите user_id пользователя для выдачи пробного периода на 24ч:",
        reply_markup=back_kb(),
    )
    await cb.answer()


@router.message(AdminState.waiting_grant_user_id)
async def handle_grant_trial(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    try:
        target_id = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Неверный user_id.")
        return
    grant_trial(target_id)
    await msg.answer(f"✅ Пробный период 24ч выдан пользователю <code>{target_id}</code>.", parse_mode="HTML")


@router.callback_query(F.data == "admin_list_users")
async def cb_admin_list_users(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    users = get_all_users()
    if not users:
        await cb.message.edit_text("Пользователей нет.", reply_markup=back_kb())
        await cb.answer()
        return
    lines = []
    for u in users[:50]:  # макс 50
        status = "✅" if u.get("is_active") else "❌"
        name = u.get("first_name") or u.get("username") or "без имени"
        lines.append(f"{status} <code>{u['user_id']}</code> {name}")
    await cb.message.edit_text(
        "👥 <b>Пользователи</b>\n\n" + "\n".join(lines),
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await cb.answer()
