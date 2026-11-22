"""
Все хендлеры бота: команды, callback-кнопки, платежи.
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
)
from bot.keyboards import main_menu_kb, buy_menu_kb, back_kb, tickers_kb

logger = logging.getLogger(__name__)
router = Router()


class AddTickerState(StatesGroup):
    waiting_for_ticker = State()


@router.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    name = msg.from_user.first_name or "трейдер"
    await msg.answer(
        f"Привет, {name}!\n\n"
        "Я AnomalyAlert - бот для отслеживания аномальных событий "
        "на Московской бирже и СПБ Бирже.\n\n"
        "Я мониторю:\n"
        "- Аномальные объёмы торгов\n"
        "- Резкие движения цены (>3%)\n"
        "- Пробои полос Боллинджера\n\n"
        "Выбери действие:",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: CallbackQuery) -> None:
    await cb.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    await cb.answer()


@router.callback_query(F.data == "about")
async def cb_about(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "О боте AnomalyAlert\n\n"
        "Бот каждую минуту сканирует рынок и присылает алерты:\n\n"
        "Аномальный объём - объём превышает среднее в 3x\n"
        "Резкое движение - цена изменилась более чем на 3%\n"
        "Полосы Боллинджера - цена вышла за 2.5 сигма\n\n"
        f"Стоимость: {SUBSCRIPTION_PRICE_STARS} Stars / месяц\n"
        "Пробный период: 24 часа бесплатно",
        reply_markup=back_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "status")
async def cb_status(cb: CallbackQuery) -> None:
    info = get_subscription_info(cb.from_user.id)
    if info:
        exp = info["expires_at"].strftime("%d.%m.%Y %H:%M UTC")
        sub_type = "Пробная" if info["type"] == "trial" else "Платная"
        text = f"Подписка активна\nТип: {sub_type}\nДействует до: {exp}"
    else:
        text = "Подписка не активна. Оформите подписку, чтобы получать алерты."
    await cb.message.edit_text(
        text, reply_markup=buy_menu_kb() if not info else back_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "trial")
async def cb_trial(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    if has_active_subscription(uid):
        await cb.answer("У вас уже есть активная подписка!", show_alert=True)
        return
    if had_trial(uid):
        await cb.answer("Пробный период уже был использован.", show_alert=True)
        return
    activate_trial(uid)
    await cb.message.edit_text(
        "Пробный период на 24 часа активирован!\n"
        "Вы будете получать алерты об аномалиях на бирже.",
        reply_markup=back_kb(),
    )
    await cb.answer("Пробный период активирован!")


@router.callback_query(F.data == "buy")
async def cb_buy(cb: CallbackQuery) -> None:
    info = get_subscription_info(cb.from_user.id)
    extra = ""
    if info:
        exp = info["expires_at"].strftime("%d.%m.%Y %H:%M UTC")
        extra = f"\n\nУ вас есть подписка до {exp}. Новый платёж продлит её."
    await cb.message.edit_text(
        f"Подписка AnomalyAlert\n\n"
        f"Стоимость: {SUBSCRIPTION_PRICE_STARS} Telegram Stars / месяц\n"
        f"Мониторинг каждые 60 секунд{extra}",
        reply_markup=buy_menu_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "pay_stars")
async def cb_pay_stars(cb: CallbackQuery, bot: Bot) -> None:
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=SUBSCRIPTION_TITLE,
        description=SUBSCRIPTION_DESCRIPTION,
        payload=f"sub_{cb.from_user.id}",
        currency="XTR",
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
        f"Оплата прошла! {p.total_amount} Stars.\n"
        "Подписка активна на 30 дней.",
        reply_markup=main_menu_kb(),
    )
    logger.info("Оплата от user_id=%d stars=%d", msg.from_user.id, p.total_amount)


@router.message(F.refunded_payment)
async def payment_refund(msg: Message) -> None:
    p = msg.refunded_payment
    deactivate_subscription(p.provider_payment_charge_id)
    await msg.answer("Возврат Stars выполнен. Подписка деактивирована.", reply_markup=main_menu_kb())


@router.callback_query(F.data == "my_tickers")
async def cb_my_tickers(cb: CallbackQuery) -> None:
    if not has_active_subscription(cb.from_user.id):
        await cb.answer("Персональные тикеры доступны только с активной подпиской.", show_alert=True)
        return
    tickers = get_user_tickers(cb.from_user.id)
    text = "Ваши тикеры (нажмите чтобы удалить):" if tickers else "Список тикеров пуст:"
    await cb.message.edit_text(text, reply_markup=tickers_kb(tickers))
    await cb.answer()


@router.callback_query(F.data == "add_ticker")
async def cb_add_ticker(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddTickerState.waiting_for_ticker)
    await cb.message.edit_text(
        "Введите тикер в формате TICKER:EXCHANGE\n\n"
        "Примеры: SBER:MOEX или AAPL:SPBEX\n"
        "Или просто тикер (например LKOH) - по умолчанию MOEX.",
        reply_markup=back_kb("my_tickers"),
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
        await msg.answer("Неверный формат тикера.", reply_markup=main_menu_kb())
        return
    exchange = exchange if exchange in ("MOEX", "SPBEX") else "MOEX"
    added = add_user_ticker(msg.from_user.id, ticker, exchange)
    if added:
        await msg.answer(f"Тикер {ticker} ({exchange}) добавлен.", reply_markup=main_menu_kb())
    else:
        await msg.answer("Тикер уже в списке или произошла ошибка.", reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("del_ticker:"))
async def cb_del_ticker(cb: CallbackQuery) -> None:
    _, ticker, exchange = cb.data.split(":", 2)
    remove_user_ticker(cb.from_user.id, ticker, exchange)
    tickers = get_user_tickers(cb.from_user.id)
    text = "Ваши тикеры:" if tickers else "Список тикеров пуст."
    await cb.message.edit_text(text, reply_markup=tickers_kb(tickers))
    await cb.answer(f"Тикер {ticker} удалён")


@router.message(Command("admin"))
async def cmd_admin(msg: Message) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    s = get_stats()
    await msg.answer(
        f"Панель администратора\n\n"
        f"Всего пользователей: {s['total_users']}\n"
        f"Активных подписок: {s['active_subs']}\n"
        f"Платных подписок: {s['paid_subs']}\n"
        f"Алертов за 24ч: {s['alerts_today']}",
    )
