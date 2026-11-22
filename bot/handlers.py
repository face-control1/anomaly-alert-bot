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
)
from bot.keyboards import main_menu_kb, buy_menu_kb, back_kb, tickers_kb

logger = logging.getLogger(__name__)
router = Router()


class AddTickerState(StatesGroup):
    waiting_for_ticker = State()


class AdminState(StatesGroup):
    waiting_new_price = State()
    waiting_grant_user_id = State()


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


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "Главное меню — выбери действие:",
        reply_markup=main_menu_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "about")
async def cb_about(cb: CallbackQuery) -> None:
    price = get_subscription_price()
    await cb.message.edit_text(
        "ℹ️ <b>О боте AnomalyAlert</b>\n\n"
        "Бот каждую минуту сканирует <b>все акции MOEX TQBR</b> (~200+ бумаг):\n\n"
        "🔊 <b>Аномальный объём</b> — объём торгов превышает среднее в 3× и более\n"
        "🚨 <b>Резкое движение</b> — цена изменилась более чем на 3% за минуту\n"
        "📈📉 <b>Полосы Боллинджера</b> — цена вышла за 2.5σ от скользящей средней\n\n"
        f"💰 Стоимость: <b>{price} ⭐ Stars / месяц</b>\n"
        "🎁 Пробный период: <b>24 часа бесплатно</b>",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


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
        "Вы будете получать алерты об аномалиях на бирже.",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await cb.answer("Пробный период активирован!")

@router.callback_query(F.data == "buy")
async def cb_buy(cb: CallbackQuery) -> None:
    info = get_subscription_info(cb.from_user.id)
    price = get_subscription_price()
    extra = ""
    if info:
        exp = info["expires_at"].strftime("%d.%m.%Y %H:%M UTC")
        extra = f"\n\n⚠️ У вас уже есть подписка до {exp}."
    await cb.message.edit_text(
        f"⭐ <b>Подписка AnomalyAlert</b>\n\n"
        f"💰 Стоимость: <b>{price} Telegram Stars</b> / месяц\n"
        f"🔄 Мониторинг каждые 60 секунд\n"
        f"📊 Все акции MOEX TQBR (~200+ бумаг){extra}",
        reply_markup=buy_menu_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data == "pay_stars")
async def cb_pay_stars(cb: CallbackQuery, bot: Bot) -> None:
    price = get_subscription_price()
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=SUBSCRIPTION_TITLE,
        description=SUBSCRIPTION_DESCRIPTION,
        payload=f"sub_{cb.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label="Подписка 30 дней", amount=price)],
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
        "Подписка активна на 30 дней. Алерты уже идут!",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


@router.message(F.refunded_payment)
async def payment_refund(msg: Message) -> None:
    p = msg.refunded_payment
    deactivate_subscription(p.provider_payment_charge_id)
    await msg.answer("↩️ Возврат Stars. Подписка деактивирована.", reply_markup=main_menu_kb())


@router.callback_query(F.data == "my_tickers")
async def cb_my_tickers(cb: CallbackQuery) -> None:
    if not has_active_subscription(cb.from_user.id):
        await cb.answer("Персональные тикеры доступны только с активной подпиской.", show_alert=True)
        return
    tickers = get_user_tickers(cb.from_user.id)
    text = "📋 <b>Ваши тикеры</b>\nНажмите на тикер, чтобы удалить:" if tickers else "📋 <b>Ваши тикеры</b>\nСписок пуст."
    await cb.message.edit_text(text, reply_markup=tickers_kb(tickers), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "add_ticker")
async def cb_add_ticker(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddTickerState.waiting_for_ticker)
    await cb.message.edit_text(
        "✏️ Введите тикер в формате <b>TICKER:EXCHANGE</b>\n\nПримеры:\n• <code>SBER:MOEX</code>\n• <code>AAPL:SPBEX</code>\n\nИли просто тикер (MOEX по умолчанию).",
        reply_markup=back_kb("my_tickers"), parse_mode="HTML")
    await cb.answer()


@router.message(AddTickerState.waiting_for_ticker)
async def handle_ticker_input(msg: Message, state: FSMContext) -> None:
    await state.clear()
    raw = msg.text.strip().upper()
    ticker, exchange = (raw.split(":", 1) if ":" in raw else (raw, "MOEX"))
    if not ticker.isalpha():
        await msg.answer("❌ Неверный формат.", reply_markup=main_menu_kb())
        return
    exchange = exchange if exchange in ("MOEX", "SPBEX") else "MOEX"
    if add_user_ticker(msg.from_user.id, ticker, exchange):
        await msg.answer(f"✅ <b>{ticker} ({exchange})</b> добавлен.", reply_markup=main_menu_kb(), parse_mode="HTML")
    else:
        await msg.answer("⚠️ Тикер уже в списке.", reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("del_ticker:"))
async def cb_del_ticker(cb: CallbackQuery) -> None:
    _, ticker, exchange = cb.data.split(":", 2)
    remove_user_ticker(cb.from_user.id, ticker, exchange)
    tickers = get_user_tickers(cb.from_user.id)
    text = "📋 <b>Ваши тикеры</b>\nНажмите на тикер, чтобы удалить:" if tickers else "📋 <b>Ваши тикеры</b>\nСписок пуст."
    await cb.message.edit_text(text, reply_markup=tickers_kb(tickers), parse_mode="HTML")
    await cb.answer(f"Тикер {ticker} удалён")


def _admin_panel_text() -> str:
    s = get_stats()
    price = get_subscription_price()
    return (
        "🛠 <b>Панель администратора</b>\n\n"
        f"👤 Всего пользователей: <b>{s['total_users']}</b>\n"
        f"✅ Активных подписок: <b>{s['active_subs']}</b>\n"
        f"💎 Платных: <b>{s['paid_subs']}</b>\n"
        f"🔔 Алертов за 24ч: <b>{s['alerts_today']}</b>\n\n"
        f"💰 Цена: <b>{price} ⭐ Stars / мес</b>"
    )


def _admin_kb():
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
    await msg.answer(_admin_panel_text(), reply_markup=_admin_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.edit_text(_admin_panel_text(), reply_markup=_admin_kb(), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "admin_set_price")
async def cb_admin_set_price(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminState.waiting_new_price)
    await cb.message.edit_text(
        f"💰 Текущая цена: <b>{get_subscription_price()} ⭐</b>\n\nВведите новую цену (целое число):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")]]),
        parse_mode="HTML")
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
        await msg.answer("❌ Неверное значение.")
        return
    set_subscription_price(new_price)
    await msg.answer(f"✅ Цена обновлена: <b>{new_price} ⭐ / мес</b>", reply_markup=_admin_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_grant_trial")
async def cb_admin_grant_trial(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminState.waiting_grant_user_id)
    await cb.message.edit_text(
        "🎁 <b>Выдать пробный период</b>\n\nВведите user_id:\n<code>123456789</code>\nИли с часами: <code>123456789 48</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")]]),
        parse_mode="HTML")
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
    except (ValueError, IndexError):
        await msg.answer("❌ Неверный формат.", parse_mode="HTML")
        return
    grant_trial(target_id, hours=hours)
    try:
        await bot.send_message(target_id, f"🎁 <b>Вам выдан пробный период на {hours} ч!</b>", parse_mode="HTML")
        note = "✉️ Пользователь уведомлён."
    except Exception:
        note = "⚠️ Не удалось уведомить."
    await msg.answer(f"✅ Trial на {hours}ч выдан <code>{target_id}</code>. {note}", reply_markup=_admin_kb(), parse_mode="HTML")


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
            sub = u.get("sub_type")
            exp_str = u["expires_at"].strftime("%d.%m") if u.get("expires_at") else "?"
            sub_str = f"{'\ud83c\udf81' if sub == 'trial' else '\ud83d\udc8e'} до {exp_str}" if sub else "❌"
            lines.append(f"<code>{u['user_id']}</code> {name} — {sub_str}")
        text = "\n".join(lines)
    await cb.message.edit_text(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]]),
        parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.edit_text("📢 <b>Рассылка</b>\n\nОтправьте текст:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")]]),
        parse_mode="HTML")
    await state.set_state(AdminState.waiting_grant_user_id)
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
    sent = sum(1 for uid in get_all_active_user_ids() if not (await bot.send_message(uid, text, parse_mode="HTML")) is None)
    await msg.answer(f"✅ Рассылка отправлена.")


@router.message(Command("id"))
async def cmd_id(msg: Message) -> None:
    await msg.answer(f"Ваш Telegram ID: <code>{msg.from_user.id}</code>", parse_mode="HTML")
