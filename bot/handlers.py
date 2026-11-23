"""
ÐÑÐµ ÑÑÐ½Ð´Ð»ÐµÑÑ Ð±Ð¾ÑÐ°: ÐºÐ¾Ð¼Ð°Ð½Ð´Ñ, callback-ÐºÐ½Ð¾Ð¿ÐºÐ¸, Ð¿Ð»Ð°ÑÐµÐ¶Ð¸.
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


# âââ /start ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@router.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    name = msg.from_user.first_name or "ÑÑÐµÐ¹Ð´ÐµÑ"
    await msg.answer(
        f"ð ÐÑÐ¸Ð²ÐµÑ, <b>{name}</b>!\n\n"
        "Ð¯ <b>AnomalyAlert</b> â Ð±Ð¾Ñ Ð´Ð»Ñ Ð¾ÑÑÐ»ÐµÐ¶Ð¸Ð²Ð°Ð½Ð¸Ñ Ð°Ð½Ð¾Ð¼Ð°Ð»ÑÐ½ÑÑ ÑÐ¾Ð±ÑÑÐ¸Ð¹ "
        "Ð½Ð° <b>ÐÐ¾ÑÐºÐ¾Ð²ÑÐºÐ¾Ð¹ Ð±Ð¸ÑÐ¶Ðµ</b> Ð¸ <b>Ð¡ÐÐ ÐÐ¸ÑÐ¶Ðµ</b>.\n\n"
        "ð Ð¯ Ð¼Ð¾Ð½Ð¸ÑÐ¾ÑÑ:\n"
        "â¢ ÐÐ½Ð¾Ð¼Ð°Ð»ÑÐ½ÑÐµ Ð¾Ð±ÑÑÐ¼Ñ ÑÐ¾ÑÐ³Ð¾Ð²\n"
        "â¢ Ð ÐµÐ·ÐºÐ¸Ðµ Ð´Ð²Ð¸Ð¶ÐµÐ½Ð¸Ñ ÑÐµÐ½Ñ (>3%)\n"
        "â¢ ÐÑÐ¾Ð±Ð¾Ð¸ Ð¿Ð¾Ð»Ð¾Ñ ÐÐ¾Ð»Ð»Ð¸Ð½Ð´Ð¶ÐµÑÐ°\n\n"
        "ÐÑÐ±ÐµÑÐ¸ Ð´ÐµÐ¹ÑÑÐ²Ð¸Ðµ:",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


# âââ ÐÐ°Ð²Ð¸Ð³Ð°ÑÐ¸Ñ ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "ÐÐ»Ð°Ð²Ð½Ð¾Ðµ Ð¼ÐµÐ½Ñ â Ð²ÑÐ±ÐµÑÐ¸ Ð´ÐµÐ¹ÑÑÐ²Ð¸Ðµ:",
        reply_markup=main_menu_kb(),
    )
    await cb.answer()


# âââ Ð¡ÑÐ°ÑÑÑ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ¸ ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

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
        exp = info["expires_at"].strftime("%d.%m.%Y %H:%M ÐÐ¡Ð")
        sub_type = "ð ÐÑÐ¾Ð±Ð½Ð°Ñ" if info["type"] == "trial" else "ð ÐÐ»Ð°ÑÐ½Ð°Ñ"
        text = (
            f"â <b>ÐÐ¾Ð´Ð¿Ð¸ÑÐºÐ° Ð°ÐºÑÐ¸Ð²Ð½Ð°</b>\n\n"
            f"Ð¢Ð¸Ð¿: {sub_type}\n"
            f"ÐÐµÐ¹ÑÑÐ²ÑÐµÑ Ð´Ð¾: <b>{exp}</b>"
        )
    else:
        text = (
            "â <b>ÐÐ¾Ð´Ð¿Ð¸ÑÐºÐ° Ð½Ðµ Ð°ÐºÑÐ¸Ð²Ð½Ð°</b>\n\n"
            "ÐÑÐ¾ÑÐ¼Ð¸ÑÐµ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÑ, ÑÑÐ¾Ð±Ñ Ð¿Ð¾Ð»ÑÑÐ°ÑÑ Ð°Ð»ÐµÑÑÑ."
        )
    await cb.message.edit_text(
        text,
        reply_markup=buy_menu_kb() if not info else back_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


# âââ ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@router.callback_query(F.data == "trial")
async def cb_trial(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    if has_active_subscription(uid):
        await cb.answer("Ð£ Ð²Ð°Ñ ÑÐ¶Ðµ ÐµÑÑÑ Ð°ÐºÑÐ¸Ð²Ð½Ð°Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ°!", show_alert=True)
        return
    if had_trial(uid):
        await cb.answer(
            "ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ ÑÐ¶Ðµ Ð±ÑÐ» Ð¸ÑÐ¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°Ð½. ÐÑÐ¾ÑÐ¼Ð¸ÑÐµ Ð¿Ð»Ð°ÑÐ½ÑÑ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÑ.",
            show_alert=True,
        )
        return
    if not activate_trial(uid):
        await cb.message.edit_text(
            "â ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ ÑÐ¶Ðµ Ð±ÑÐ» Ð¸ÑÐ¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°Ð½.\n\nÐÐ»Ñ Ð¿ÑÐ¾Ð´Ð¾Ð»Ð¶ÐµÐ½Ð¸Ñ Ð¾ÑÐ¾ÑÐ¼Ð¸ÑÐµ Ð¿Ð»Ð°ÑÐ½ÑÑ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÑ.",
            reply_markup=back_kb(),
            parse_mode="HTML",
        )
        await cb.answer()
        return
    await cb.message.edit_text(
        "ð <b>ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ Ð½Ð° 24 ÑÐ°ÑÐ° Ð°ÐºÑÐ¸Ð²Ð¸ÑÐ¾Ð²Ð°Ð½!</b>\n\n"
        "ÐÑ Ð±ÑÐ´ÐµÑÐµ Ð¿Ð¾Ð»ÑÑÐ°ÑÑ Ð°Ð»ÐµÑÑÑ Ð¾Ð± Ð°Ð½Ð¾Ð¼Ð°Ð»Ð¸ÑÑ Ð½Ð° Ð±Ð¸ÑÐ¶Ðµ.\n"
        "ÐÐ¾ÑÐ»Ðµ Ð¾ÐºÐ¾Ð½ÑÐ°Ð½Ð¸Ñ Ð¿ÑÐ¾Ð±Ð½Ð¾Ð³Ð¾ Ð¿ÐµÑÐ¸Ð¾Ð´Ð° Ð¾ÑÐ¾ÑÐ¼Ð¸ÑÐµ Ð¿Ð»Ð°ÑÐ½ÑÑ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÑ.",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await cb.answer("ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ Ð°ÐºÑÐ¸Ð²Ð¸ÑÐ¾Ð²Ð°Ð½!")


# âââ ÐÐ¾ÐºÑÐ¿ÐºÐ° Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ¸ âââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@router.callback_query(F.data == "buy")
async def cb_buy(cb: CallbackQuery) -> None:
    info = get_subscription_info(cb.from_user.id)
    extra = ""
    if info:
        exp = info["expires_at"].strftime("%d.%m.%Y %H:%M ÐÐ¡Ð")
        extra = f"\n\nâ ï¸ Ð£ Ð²Ð°Ñ ÑÐ¶Ðµ ÐµÑÑÑ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ° Ð´Ð¾ {exp}. ÐÐ¾Ð²ÑÐ¹ Ð¿Ð»Ð°ÑÑÐ¶ Ð¿ÑÐ¾Ð´Ð»Ð¸Ñ ÐµÑ."
    await cb.message.edit_text(
        f"â­ <b>ÐÐ¾Ð´Ð¿Ð¸ÑÐºÐ° AnomalyAlert</b>\n\n"
        f"ð° Ð¡ÑÐ¾Ð¸Ð¼Ð¾ÑÑÑ: <b>{SUBSCRIPTION_PRICE_STARS} Telegram Stars</b> / Ð¼ÐµÑÑÑ\n"
        f"ð ÐÐ¾Ð½Ð¸ÑÐ¾ÑÐ¸Ð½Ð³ ÐºÐ°Ð¶Ð´ÑÐµ 60 ÑÐµÐºÑÐ½Ð´\n"
        f"ð Ð¢Ð¾Ð¿-15 Ð°ÐºÑÐ¸Ð¹ ÐÐÐÐ + Ð¿ÐµÑÑÐ¾Ð½Ð°Ð»ÑÐ½ÑÐµ ÑÐ¸ÐºÐµÑÑ{extra}",
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
        prices=[LabeledPrice(label="ÐÐ¾Ð´Ð¿Ð¸ÑÐºÐ° 30 Ð´Ð½ÐµÐ¹", amount=SUBSCRIPTION_PRICE_STARS)],
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
        "â <b>ÐÐ¿Ð»Ð°ÑÐ° Ð¿ÑÐ¾ÑÐ»Ð° ÑÑÐ¿ÐµÑÐ½Ð¾!</b>\n\n"
        f"ÐÑ Ð¾Ð¿Ð»Ð°ÑÐ¸Ð»Ð¸ <b>{p.total_amount} â­ Stars</b>.\n"
        "ÐÐ¾Ð´Ð¿Ð¸ÑÐºÐ° Ð°ÐºÑÐ¸Ð²Ð½Ð° Ð½Ð° 30 Ð´Ð½ÐµÐ¹. ÐÐ»ÐµÑÑÑ ÑÐ¶Ðµ Ð¸Ð´ÑÑ!\n\n"
        "ÐÐ°ÑÑÑÐ¾Ð¹ÑÐµ Ð¿ÐµÑÑÐ¾Ð½Ð°Ð»ÑÐ½ÑÐµ ÑÐ¸ÐºÐµÑÑ ÑÐµÑÐµÐ· Ð¼ÐµÐ½Ñ Â«ð ÐÐ¾Ð¸ ÑÐ¸ÐºÐµÑÑÂ».",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
    logger.info("ÐÐ¿Ð»Ð°ÑÐ° Ð¾Ñ user_id=%d, charge_id=%s, stars=%d",
                msg.from_user.id, p.provider_payment_charge_id, p.total_amount)


@router.message(F.refunded_payment)
async def payment_refund(msg: Message) -> None:
    p = msg.refunded_payment
    deactivate_subscription(p.provider_payment_charge_id)
    await msg.answer(
        "â©ï¸ ÐÐ¾Ð·Ð²ÑÐ°Ñ Stars Ð²ÑÐ¿Ð¾Ð»Ð½ÐµÐ½. ÐÐ°ÑÐ° Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ° Ð´ÐµÐ°ÐºÑÐ¸Ð²Ð¸ÑÐ¾Ð²Ð°Ð½Ð°.",
        reply_markup=main_menu_kb(),
    )


# âââ ÐÐµÑÑÐ¾Ð½Ð°Ð»ÑÐ½ÑÐµ ÑÐ¸ÐºÐµÑÑ ââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@router.callback_query(F.data == "my_tickers")
async def cb_my_tickers(cb: CallbackQuery) -> None:
    if not has_active_subscription(cb.from_user.id):
        await cb.answer(
            "ÐÐµÑÑÐ¾Ð½Ð°Ð»ÑÐ½ÑÐµ ÑÐ¸ÐºÐµÑÑ Ð´Ð¾ÑÑÑÐ¿Ð½Ñ ÑÐ¾Ð»ÑÐºÐ¾ Ñ Ð°ÐºÑÐ¸Ð²Ð½Ð¾Ð¹ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ¾Ð¹.",
            show_alert=True,
        )
        return
    tickers = get_user_tickers(cb.from_user.id)
    if tickers:
        text = "ð <b>ÐÐ°ÑÐ¸ ÑÐ¸ÐºÐµÑÑ</b>\nÐÐ°Ð¶Ð¼Ð¸ÑÐµ Ð½Ð° ÑÐ¸ÐºÐµÑ, ÑÑÐ¾Ð±Ñ ÑÐ´Ð°Ð»Ð¸ÑÑ ÐµÐ³Ð¾:"
    else:
        text = "ð <b>ÐÐ°ÑÐ¸ ÑÐ¸ÐºÐµÑÑ</b>\nÐ¡Ð¿Ð¸ÑÐ¾Ðº Ð¿ÑÑÑ. ÐÐ¾Ð±Ð°Ð²ÑÑÐµ ÑÐ¸ÐºÐµÑÑ Ð´Ð»Ñ Ð¾ÑÑÐ»ÐµÐ¶Ð¸Ð²Ð°Ð½Ð¸Ñ:"
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
        "âï¸ ÐÐ²ÐµÐ´Ð¸ÑÐµ ÑÐ¸ÐºÐµÑ Ð² ÑÐ¾ÑÐ¼Ð°ÑÐµ <b>TICKER:EXCHANGE</b>\n\n"
        "ÐÑÐ¸Ð¼ÐµÑÑ:\n"
        "â¢ <code>SBER:MOEX</code>\n"
        "â¢ <code>AAPL:SPBEX</code>\n\n"
        "ÐÐ»Ð¸ Ð¿ÑÐ¾ÑÑÐ¾ ÑÐ¸ÐºÐµÑ (Ð½Ð°Ð¿ÑÐ¸Ð¼ÐµÑ <code>LKOH</code>) â Ð¿Ð¾ ÑÐ¼Ð¾Ð»ÑÐ°Ð½Ð¸Ñ MOEX.",
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
        await msg.answer("â ÐÐµÐ²ÐµÑÐ½ÑÐ¹ ÑÐ¾ÑÐ¼Ð°Ñ ÑÐ¸ÐºÐµÑÐ°.", reply_markup=main_menu_kb())
        return

    exchange = exchange if exchange in ("MOEX", "SPBEX") else "MOEX"
    added = add_user_ticker(msg.from_user.id, ticker, exchange)
    if added:
        await msg.answer(
            f"â Ð¢Ð¸ÐºÐµÑ <b>{ticker} ({exchange})</b> Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½ Ð² Ð¾ÑÑÐ»ÐµÐ¶Ð¸Ð²Ð°Ð½Ð¸Ðµ.",
            reply_markup=main_menu_kb(),
            parse_mode="HTML",
        )
    else:
        await msg.answer("â ï¸ Ð¢Ð¸ÐºÐµÑ ÑÐ¶Ðµ Ð² ÑÐ¿Ð¸ÑÐºÐµ Ð¸Ð»Ð¸ Ð¿ÑÐ¾Ð¸Ð·Ð¾ÑÐ»Ð° Ð¾ÑÐ¸Ð±ÐºÐ°.",
                         reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("del_ticker:"))
async def cb_del_ticker(cb: CallbackQuery) -> None:
    _, ticker, exchange = cb.data.split(":", 2)
    remove_user_ticker(cb.from_user.id, ticker, exchange)
    tickers = get_user_tickers(cb.from_user.id)
    if tickers:
        text = "ð <b>ÐÐ°ÑÐ¸ ÑÐ¸ÐºÐµÑÑ</b>\nÐÐ°Ð¶Ð¼Ð¸ÑÐµ Ð½Ð° ÑÐ¸ÐºÐµÑ, ÑÑÐ¾Ð±Ñ ÑÐ´Ð°Ð»Ð¸ÑÑ ÐµÐ³Ð¾:"
    else:
        text = "ð <b>ÐÐ°ÑÐ¸ ÑÐ¸ÐºÐµÑÑ</b>\nÐ¡Ð¿Ð¸ÑÐ¾Ðº Ð¿ÑÑÑ."
    await cb.message.edit_text(
        text,
        reply_markup=tickers_kb(tickers),
        parse_mode="HTML",
    )
    await cb.answer(f"Ð¢Ð¸ÐºÐµÑ {ticker} ÑÐ´Ð°Ð»ÑÐ½")


# âââ ÐÐ·Ð±ÑÐ°Ð½Ð½ÑÐµ Ð°ÐºÑÐ¸Ð¸ ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def _favs_text(favs: list[dict]) -> str:
    if favs:
        return (
            "ð <b>ÐÐ·Ð±ÑÐ°Ð½Ð½ÑÐµ Ð°ÐºÑÐ¸Ð¸</b>\n\n"
            "ÐÐ»ÐµÑÑÑ Ð±ÑÐ´ÑÑ Ð¿ÑÐ¸ÑÐ¾Ð´Ð¸ÑÑ <b>ÑÐ¾Ð»ÑÐºÐ¾ Ð¿Ð¾ ÑÑÐ¸Ð¼ Ð°ÐºÑÐ¸ÑÐ¼</b>.\n"
            "ÐÐ°Ð¶Ð¼Ð¸ÑÐµ Ð½Ð° Ð°ÐºÑÐ¸Ñ, ÑÑÐ¾Ð±Ñ ÑÐ´Ð°Ð»Ð¸ÑÑ ÐµÑ Ð¸Ð· Ð¸Ð·Ð±ÑÐ°Ð½Ð½Ð¾Ð³Ð¾:"
        )
    return (
        "ð <b>ÐÐ·Ð±ÑÐ°Ð½Ð½ÑÐµ Ð°ÐºÑÐ¸Ð¸</b>\n\n"
        "Ð¡Ð¿Ð¸ÑÐ¾Ðº Ð¿ÑÑÑ. ÐÐ¾ÐºÐ° Ð¸Ð·Ð±ÑÐ°Ð½Ð½Ð¾Ðµ Ð¿ÑÑÑÐ¾Ðµ â Ð²Ñ Ð¿Ð¾Ð»ÑÑÐ°ÐµÑÐµ "
        "<b>Ð²ÑÐµ Ð°Ð»ÐµÑÑÑ Ð¿Ð¾ ÑÑÐ½ÐºÑ</b>.\n\n"
        "ÐÐ¾Ð±Ð°Ð²ÑÑÐµ Ð°ÐºÑÐ¸Ð¸, ÑÑÐ¾Ð±Ñ Ð¿Ð¾Ð»ÑÑÐ°ÑÑ Ð°Ð»ÐµÑÑÑ ÑÐ¾Ð»ÑÐºÐ¾ Ð¿Ð¾ Ð½Ð¸Ð¼."
    )


@router.callback_query(F.data == "favorites")
async def cb_favorites(cb: CallbackQuery) -> None:
    if not has_active_subscription(cb.from_user.id):
        await cb.answer(
            "ÐÐ·Ð±ÑÐ°Ð½Ð½Ð¾Ðµ Ð´Ð¾ÑÑÑÐ¿Ð½Ð¾ ÑÐ¾Ð»ÑÐºÐ¾ Ñ Ð°ÐºÑÐ¸Ð²Ð½Ð¾Ð¹ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ¾Ð¹.",
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
        "âï¸ <b>ÐÐ¾Ð±Ð°Ð²Ð¸ÑÑ Ð°ÐºÑÐ¸Ñ Ð² Ð¸Ð·Ð±ÑÐ°Ð½Ð½Ð¾Ðµ</b>\n\n"
        "ÐÐ²ÐµÐ´Ð¸ÑÐµ ÑÐ¸ÐºÐµÑ Ð°ÐºÑÐ¸Ð¸ (ÑÐ¾Ð»ÑÐºÐ¾ MOEX):\n\n"
        "ÐÑÐ¸Ð¼ÐµÑÑ: <code>SBER</code>, <code>LKOH</code>, <code>GAZP</code>",
        reply_markup=back_kb("favorites"),
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(AddFavoriteState.waiting_for_ticker)
async def handle_favorite_input(msg: Message, state: FSMContext) -> None:
    await state.clear()
    ticker = msg.text.strip().upper()
    # Ð£Ð±Ð¸ÑÐ°ÐµÐ¼ Ð»Ð¸ÑÐ½ÐµÐµ (ÐµÑÐ»Ð¸ Ð²Ð²ÐµÐ»Ð¸ SBER:MOEX â Ð±ÐµÑÑÐ¼ ÑÐ¾Ð»ÑÐºÐ¾ ÑÐ¸ÐºÐµÑ)
    if ":" in ticker:
        ticker = ticker.split(":")[0]

    if not ticker.isalpha() or len(ticker) > 10:
        await msg.answer(
            "â ÐÐµÐ²ÐµÑÐ½ÑÐ¹ ÑÐ¾ÑÐ¼Ð°Ñ. ÐÐ²ÐµÐ´Ð¸ÑÐµ ÑÐ¸ÐºÐµÑ Ð±ÑÐºÐ²Ð°Ð¼Ð¸, Ð½Ð°Ð¿ÑÐ¸Ð¼ÐµÑ: <code>SBER</code>",
            reply_markup=main_menu_kb(),
            parse_mode="HTML",
        )
        return

    added = add_favorite(msg.from_user.id, ticker, "MOEX")
    if added:
        await msg.answer(
            f"â <b>{ticker}</b> Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½ Ð² Ð¸Ð·Ð±ÑÐ°Ð½Ð½Ð¾Ðµ!\n\n"
            "Ð¢ÐµÐ¿ÐµÑÑ Ð²Ñ Ð±ÑÐ´ÐµÑÐµ Ð¿Ð¾Ð»ÑÑÐ°ÑÑ Ð°Ð»ÐµÑÑÑ ÑÐ¾Ð»ÑÐºÐ¾ Ð¿Ð¾ Ð°ÐºÑÐ¸ÑÐ¼ Ð¸Ð· Ð¸Ð·Ð±ÑÐ°Ð½Ð½Ð¾Ð³Ð¾.",
            reply_markup=main_menu_kb(),
            parse_mode="HTML",
        )
    else:
        await msg.answer(
            "â ï¸ ÐÐºÑÐ¸Ñ ÑÐ¶Ðµ Ð² Ð¸Ð·Ð±ÑÐ°Ð½Ð½Ð¾Ð¼.",
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
    await cb.answer(f"{ticker} ÑÐ´Ð°Ð»ÑÐ½ Ð¸Ð· Ð¸Ð·Ð±ÑÐ°Ð½Ð½Ð¾Ð³Ð¾")


# âââ ÐÐ¾Ð´Ð´ÐµÑÐ¶ÐºÐ° ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@router.callback_query(F.data == "support")
async def cb_support(cb: CallbackQuery) -> None:
    if SUPPORT_USERNAME:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="âï¸ ÐÐ°Ð¿Ð¸ÑÐ°ÑÑ Ð² Ð¿Ð¾Ð´Ð´ÐµÑÐ¶ÐºÑ",
                url=f"https://t.me/{SUPPORT_USERNAME}",
            )],
            [InlineKeyboardButton(text="ð ÐÐ°Ð·Ð°Ð´", callback_data="main_menu")],
        ])
        text = (
            "ð¬ <b>ÐÐ¾Ð´Ð´ÐµÑÐ¶ÐºÐ°</b>\n\n"
            "ÐÑÐ»Ð¸ Ñ Ð²Ð°Ñ Ð²Ð¾Ð·Ð½Ð¸ÐºÐ»Ð¸ Ð²Ð¾Ð¿ÑÐ¾ÑÑ Ð¸Ð»Ð¸ Ð¿ÑÐ¾Ð±Ð»ÐµÐ¼Ñ â Ð½Ð°Ð¿Ð¸ÑÐ¸ÑÐµ Ð½Ð°Ð¼!\n\n"
            "ÐÑ Ð¾ÑÐ²ÐµÑÐ°ÐµÐ¼ Ð² ÑÐµÑÐµÐ½Ð¸Ðµ 24 ÑÐ°ÑÐ¾Ð²."
        )
    else:
        kb = back_kb()
        text = "ð¬ <b>ÐÐ¾Ð´Ð´ÐµÑÐ¶ÐºÐ°</b>\n\nÐÐ¾Ð´Ð´ÐµÑÐ¶ÐºÐ° Ð²ÑÐµÐ¼ÐµÐ½Ð½Ð¾ Ð½ÐµÐ´Ð¾ÑÑÑÐ¿Ð½Ð°. ÐÐ¾Ð¿ÑÐ¾Ð±ÑÐ¹ÑÐµ Ð¿Ð¾Ð·Ð¶Ðµ."
    await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await cb.answer()


# âââ ÐÐ¾Ð¼Ð°Ð½Ð´Ñ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾ÑÐ° âââââââââââââââââââââââââââââââââââââââââââââââââââ

def _admin_panel_text() -> str:
    s = get_stats()
    price = get_subscription_price()
    return (
        "ð  <b>ÐÐ°Ð½ÐµÐ»Ñ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾ÑÐ°</b>\n\n"
        f"ð¤ ÐÑÐµÐ³Ð¾ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»ÐµÐ¹: <b>{s['total_users']}</b>\n"
        f"â ÐÐºÑÐ¸Ð²Ð½ÑÑ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐ¾Ðº: <b>{s['active_subs']}</b>\n"
        f"ð ÐÐ»Ð°ÑÐ½ÑÑ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐ¾Ðº: <b>{s['paid_subs']}</b>\n"
        f"ð ÐÐ»ÐµÑÑÐ¾Ð² Ð·Ð° 24Ñ: <b>{s['alerts_today']}</b>\n\n"
        f"ð° Ð¢ÐµÐºÑÑÐ°Ñ ÑÐµÐ½Ð°: <b>{price} â­ Stars / Ð¼ÐµÑ</b>"
    )


def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ð° ÐÐ·Ð¼ÐµÐ½Ð¸ÑÑ ÑÐµÐ½Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ¸", callback_data="admin_set_price")],
        [InlineKeyboardButton(text="ð ÐÑÐ´Ð°ÑÑ trial Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ", callback_data="admin_grant_trial")],
        [InlineKeyboardButton(text="ð¥ Ð¡Ð¿Ð¸ÑÐ¾Ðº Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»ÐµÐ¹", callback_data="admin_users")],
        [InlineKeyboardButton(text="ð¢ Ð Ð°ÑÑÑÐ»ÐºÐ°", callback_data="admin_broadcast")],
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
        await cb.answer("ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°", show_alert=True)
        return
    await cb.message.edit_text(
        _admin_panel_text(),
        reply_markup=_admin_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


# ââ ÐÐ·Ð¼ÐµÐ½Ð¸ÑÑ ÑÐµÐ½Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ¸ ââââââââââââââââââââââââââââââââââââââââââââââââââââ

@router.callback_query(F.data == "admin_set_price")
async def cb_admin_set_price(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°", show_alert=True)
        return
    current = get_subscription_price()
    await state.set_state(AdminState.waiting_new_price)
    await cb.message.edit_text(
        f"ð° Ð¢ÐµÐºÑÑÐ°Ñ ÑÐµÐ½Ð°: <b>{current} â­ Stars</b>\n\n"
        "ÐÐ²ÐµÐ´Ð¸ÑÐµ Ð½Ð¾Ð²ÑÑ ÑÐµÐ½Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ¸ Ð² Stars (ÑÐµÐ»Ð¾Ðµ ÑÐ¸ÑÐ»Ð¾, Ð¼Ð¸Ð½Ð¸Ð¼ÑÐ¼ 1):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="âï¸ ÐÑÐ¼ÐµÐ½Ð°", callback_data="admin_panel")]
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
        await msg.answer("â ÐÐµÐ²ÐµÑÐ½Ð¾Ðµ Ð·Ð½Ð°ÑÐµÐ½Ð¸Ðµ. ÐÐ²ÐµÐ´Ð¸ÑÐµ ÑÐµÐ»Ð¾Ðµ Ð¿Ð¾Ð»Ð¾Ð¶Ð¸ÑÐµÐ»ÑÐ½Ð¾Ðµ ÑÐ¸ÑÐ»Ð¾.")
        return
    set_subscription_price(new_price)
    await msg.answer(
        f"â Ð¦ÐµÐ½Ð° Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ¸ Ð¾Ð±Ð½Ð¾Ð²Ð»ÐµÐ½Ð°: <b>{new_price} â­ Stars / Ð¼ÐµÑÑÑ</b>",
        reply_markup=_admin_kb(),
        parse_mode="HTML",
    )


# ââ ÐÑÐ´Ð°ÑÑ trial Ð²ÑÑÑÐ½ÑÑ ââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@router.callback_query(F.data == "admin_grant_trial")
async def cb_admin_grant_trial(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°", show_alert=True)
        return
    await state.set_state(AdminState.waiting_grant_user_id)
    await cb.message.edit_text(
        "ð <b>ÐÑÐ´Ð°ÑÑ Ð¿ÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´</b>\n\n"
        "ÐÐ²ÐµÐ´Ð¸ÑÐµ <b>user_id</b> Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ (Ð¼Ð¾Ð¶Ð½Ð¾ ÑÐ·Ð½Ð°ÑÑ ÐºÐ¾Ð¼Ð°Ð½Ð´Ð¾Ð¹ /id Ñ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ).\n\n"
        "Ð¤Ð¾ÑÐ¼Ð°Ñ: <code>123456789</code>\n"
        "ÐÐ»Ð¸ Ñ ÑÐºÐ°Ð·Ð°Ð½Ð¸ÐµÐ¼ ÑÐ°ÑÐ¾Ð²: <code>123456789 48</code> (48 ÑÐ°ÑÐ¾Ð²)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="âï¸ ÐÑÐ¼ÐµÐ½Ð°", callback_data="admin_panel")]
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
            "â ÐÐµÐ²ÐµÑÐ½ÑÐ¹ ÑÐ¾ÑÐ¼Ð°Ñ. ÐÑÐ¸Ð¼ÐµÑ: <code>123456789</code> Ð¸Ð»Ð¸ <code>123456789 48</code>",
            parse_mode="HTML",
        )
        return

    grant_trial(target_id, hours=hours)

    # Ð£Ð²ÐµÐ´Ð¾Ð¼Ð»ÑÐµÐ¼ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ
    try:
        await bot.send_message(
            target_id,
            f"ð <b>ÐÐ°Ð¼ Ð²ÑÐ´Ð°Ð½ Ð¿ÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ Ð½Ð° {hours} Ñ!</b>\n\n"
            "Ð¢ÐµÐ¿ÐµÑÑ Ð²Ñ Ð±ÑÐ´ÐµÑÐµ Ð¿Ð¾Ð»ÑÑÐ°ÑÑ Ð°Ð»ÐµÑÑÑ Ð¾Ð± Ð°Ð½Ð¾Ð¼Ð°Ð»Ð¸ÑÑ Ð½Ð° Ð±Ð¸ÑÐ¶Ðµ.",
            parse_mode="HTML",
        )
        notified = "âï¸ ÐÐ¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ ÑÐ²ÐµÐ´Ð¾Ð¼Ð»ÑÐ½."
    except Exception:
        notified = "â ï¸ ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ ÑÐ²ÐµÐ´Ð¾Ð¼Ð¸ÑÑ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ (Ð²Ð¾Ð·Ð¼Ð¾Ð¶Ð½Ð¾, Ð·Ð°Ð±Ð»Ð¾ÐºÐ¸ÑÐ¾Ð²Ð°Ð» Ð±Ð¾ÑÐ°)."

    await msg.answer(
        f"â ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ Ð½Ð° <b>{hours} Ñ</b> Ð²ÑÐ´Ð°Ð½ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ <code>{target_id}</code>.\n{notified}",
        reply_markup=_admin_kb(),
        parse_mode="HTML",
    )


# ââ Ð¡Ð¿Ð¸ÑÐ¾Ðº Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»ÐµÐ¹ ââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@router.callback_query(F.data == "admin_users")
async def cb_admin_users(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°", show_alert=True)
        return
    users = get_all_users()
    if not users:
        text = "ð¥ ÐÐ¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»ÐµÐ¹ Ð¿Ð¾ÐºÐ° Ð½ÐµÑ."
    else:
        lines = ["ð¥ <b>ÐÐ¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ð¸</b>\n"]
        for u in users[:30]:
            name = u.get("first_name") or u.get("username") or "â"
            uid = u["user_id"]
            sub = u.get("sub_type")
            if sub:
                exp = u["expires_at"].strftime("%d.%m") if u["expires_at"] else "?"
                sub_str = f"{'ð' if sub == 'trial' else 'ð'} Ð´Ð¾ {exp}"
            else:
                sub_str = "â Ð½ÐµÑ"
            lines.append(f"<code>{uid}</code> {name} â {sub_str}")
        text = "\n".join(lines)
    await cb.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="âï¸ ÐÐ°Ð·Ð°Ð´", callback_data="admin_panel")]
        ]),
        parse_mode="HTML",
    )
    await cb.answer()


# ââ Ð Ð°ÑÑÑÐ»ÐºÐ° ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("ÐÐµÑ Ð´Ð¾ÑÑÑÐ¿Ð°", show_alert=True)
        return
    await cb.message.edit_text(
        "ð¢ <b>Ð Ð°ÑÑÑÐ»ÐºÐ°</b>\n\nÐÑÐ¿ÑÐ°Ð²ÑÑÐµ ÑÐµÐºÑÑ ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ñ (HTML ÑÐ°Ð·ÑÐµÑÑÐ½):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="âï¸ ÐÑÐ¼ÐµÐ½Ð°", callback_data="admin_panel")]
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
        await msg.answer("ÐÑÐ¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°Ð½Ð¸Ðµ: /broadcast <ÑÐµÐºÑÑ>")
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
    await msg.answer(f"â Ð Ð°ÑÑÑÐ»ÐºÐ° Ð¾ÑÐ¿ÑÐ°Ð²Ð»ÐµÐ½Ð° {sent} Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»ÑÐ¼.")


# ââ /id â ÑÐ·Ð½Ð°ÑÑ ÑÐ²Ð¾Ð¹ user_id âââââââââââââââââââââââââââââââââââââââââââââââââ

@router.message(Command("id"))
async def cmd_id(msg: Message) -> None:
    await msg.answer(f"ÐÐ°Ñ Telegram ID: <code>{msg.from_user.id}</code>", parse_mode="HTML")


# âââ Ð®Kassa: Ð¾Ð¿Ð»Ð°ÑÐ° ÐºÐ°ÑÑÐ¾Ð¹ / Ð¡ÐÐ âââââââââââââââââââââââââââââââââââââââââââââ

def _yukassa_plans_kb() -> InlineKeyboardMarkup:
    """ÐÐ»Ð°Ð²Ð¸Ð°ÑÑÑÐ° Ð²ÑÐ±Ð¾ÑÐ° ÑÐ°ÑÐ¸ÑÐ° Ð®Kassa."""
    try:
        from bot.payments_yukassa import PLANS
        buttons = []
        for key, plan in PLANS.items():
            buttons.append([InlineKeyboardButton(
                text=f"ð³ {plan['label']} â {plan['price']} â½",
                callback_data=f"yk_plan:{key}",
            )])
        buttons.append([InlineKeyboardButton(text="ð ÐÐ°Ð·Ð°Ð´", callback_data="buy")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    except ImportError:
        return back_kb("buy")


@router.callback_query(F.data == "pay_yukassa")
async def cb_pay_yukassa(cb: CallbackQuery) -> None:
    """ÐÐ¾ÐºÐ°Ð·ÑÐ²Ð°ÐµÑ ÑÐ°ÑÐ¸ÑÑ Ð®Kassa."""
    try:
        from bot.payments_yukassa import YUKASSA_AVAILABLE
        if not YUKASSA_AVAILABLE:
            await cb.answer(
                "ÐÐ¿Ð»Ð°ÑÐ° ÐºÐ°ÑÑÐ¾Ð¹ Ð²ÑÐµÐ¼ÐµÐ½Ð½Ð¾ Ð½ÐµÐ´Ð¾ÑÑÑÐ¿Ð½Ð°. ÐÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ÑÐµ Telegram Stars.",
                show_alert=True,
            )
            return
    except ImportError:
        await cb.answer("ÐÐ¿Ð»Ð°ÑÐ° ÐºÐ°ÑÑÐ¾Ð¹ Ð½ÐµÐ´Ð¾ÑÑÑÐ¿Ð½Ð°.", show_alert=True)
        return

    await cb.message.edit_text(
        "ð³ <b>ÐÐ¿Ð»Ð°ÑÐ° ÐºÐ°ÑÑÐ¾Ð¹ / Ð¡ÐÐ</b>\n\n"
        "ÐÑÐ±ÐµÑÐ¸ÑÐµ ÑÐ°ÑÐ¸Ñ:\n\n"
        "â ÐÐ¿Ð»Ð°ÑÐ° ÑÐµÑÐµÐ· Ð±Ð°Ð½ÐºÐ¾Ð²ÑÐºÑÑ ÐºÐ°ÑÑÑ Ð¸Ð»Ð¸ Ð¡ÐÐ\n"
        "ð ÐÐµÐ·Ð¾Ð¿Ð°ÑÐ½Ð°Ñ Ð¾Ð¿Ð»Ð°ÑÐ° ÑÐµÑÐµÐ· Ð®Kassa",
        reply_markup=_yukassa_plans_kb(),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("yk_plan:"))
async def cb_yukassa_plan(cb: CallbackQuery) -> None:
    """Ð¡Ð¾Ð·Ð´Ð°ÑÑ Ð¿Ð»Ð°ÑÑÐ¶ Ð®Kassa Ð´Ð»Ñ Ð²ÑÐ±ÑÐ°Ð½Ð½Ð¾Ð³Ð¾ ÑÐ°ÑÐ¸ÑÐ°."""
    plan_key = cb.data.split(":", 1)[1]
    try:
        from bot.payments_yukassa import PLANS, create_payment
    except ImportError:
        await cb.answer("ÐÐ¿Ð»Ð°ÑÐ° Ð½ÐµÐ´Ð¾ÑÑÑÐ¿Ð½Ð°.", show_alert=True)
        return

    plan = PLANS.get(plan_key)
    if not plan:
        await cb.answer("ÐÐµÐ¸Ð·Ð²ÐµÑÑÐ½ÑÐ¹ ÑÐ°ÑÐ¸Ñ.", show_alert=True)
        return

    await cb.answer("Ð¡Ð¾Ð·Ð´Ð°ÑÐ¼ ÑÑÑÐ»ÐºÑ Ð½Ð° Ð¾Ð¿Ð»Ð°ÑÑ...", show_alert=False)

    # return_url â ÑÑÑÐ»ÐºÐ° Ð½Ð° Ð±Ð¾ÑÐ° (Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ Ð²ÐµÑÐ½ÑÑÑÑ ÑÑÐ´Ð° Ð¿Ð¾ÑÐ»Ðµ Ð¾Ð¿Ð»Ð°ÑÑ)
    return_url = f"https://t.me/{(await cb.bot.get_me()).username}"
    result = await create_payment(cb.from_user.id, plan_key, return_url)

    if not result:
        await cb.message.edit_text(
            "â ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ ÑÐ¾Ð·Ð´Ð°ÑÑ Ð¿Ð»Ð°ÑÑÐ¶. ÐÐ¾Ð¿ÑÐ¾Ð±ÑÐ¹ÑÐµ Ð¿Ð¾Ð·Ð¶Ðµ Ð¸Ð»Ð¸ Ð¾Ð¿Ð»Ð°ÑÐ¸ÑÐµ ÑÐµÑÐµÐ· Telegram Stars.",
            reply_markup=back_kb("buy"),
        )
        return

    pay_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"ð³ ÐÐ¿Ð»Ð°ÑÐ¸ÑÑ {plan['price']} â½",
            url=result["confirmation_url"],
        )],
        [InlineKeyboardButton(text="ð ÐÐ°Ð·Ð°Ð´", callback_data="buy")],
    ])

    await cb.message.edit_text(
        f"ð³ <b>ÐÐ¿Ð»Ð°ÑÐ° ÑÐµÑÐµÐ· Ð®Kassa</b>\n\n"
        f"Ð¢Ð°ÑÐ¸Ñ: <b>{plan['label']}</b>\n"
        f"Ð¡ÑÐ¼Ð¼Ð°: <b>{plan['price']} â½</b>\n\n"
        "ÐÐ°Ð¶Ð¼Ð¸ÑÐµ ÐºÐ½Ð¾Ð¿ÐºÑ Ð½Ð¸Ð¶Ðµ Ð´Ð»Ñ Ð¿ÐµÑÐµÑÐ¾Ð´Ð° Ðº Ð¾Ð¿Ð»Ð°ÑÐµ.\n"
        "ÐÐ¾ÑÐ»Ðµ Ð¾Ð¿Ð»Ð°ÑÑ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ° Ð°ÐºÑÐ¸Ð²Ð¸ÑÑÐµÑÑÑ Ð°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸ÑÐµÑÐºÐ¸.",
        reply_markup=pay_kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "trial_yukassa")
async def cb_trial_yukassa(cb: CallbackQuery) -> None:
    """ÐÐºÑÐ¸Ð²Ð¸ÑÑÐµÑ Ð¿ÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ (Ð®Kassa Ð²Ð°ÑÐ¸Ð°Ð½Ñ)."""
    try:
        from bot.payments_yukassa import activate_trial, is_subscribed, TRIAL_DAYS
    except ImportError:
        await cb.answer("ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ Ð½ÐµÐ´Ð¾ÑÑÑÐ¿ÐµÐ½.", show_alert=True)
        return

    uid = cb.from_user.id
    if is_subscribed(uid):
        await cb.answer("Ð£ Ð²Ð°Ñ ÑÐ¶Ðµ ÐµÑÑÑ Ð°ÐºÑÐ¸Ð²Ð½Ð°Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ°!", show_alert=True)
        return

    ok = activate_trial(uid)
    if ok:
        await cb.message.edit_text(
            f"ð <b>ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ Ð½Ð° {TRIAL_DAYS} Ð´Ð½ÐµÐ¹ Ð°ÐºÑÐ¸Ð²Ð¸ÑÐ¾Ð²Ð°Ð½!</b>\n\n"
            "ÐÑ Ð±ÑÐ´ÐµÑÐµ Ð¿Ð¾Ð»ÑÑÐ°ÑÑ Ð²ÑÐµ Ð°Ð»ÐµÑÑÑ Ð¾Ð± Ð°Ð½Ð¾Ð¼Ð°Ð»Ð¸ÑÑ Ð½Ð° Ð±Ð¸ÑÐ¶Ðµ.\n"
            "ÐÐ¾ÑÐ»Ðµ Ð¾ÐºÐ¾Ð½ÑÐ°Ð½Ð¸Ñ Ð¿ÐµÑÐ¸Ð¾Ð´Ð° Ð¾ÑÐ¾ÑÐ¼Ð¸ÑÐµ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÑ ÐºÐ¾Ð¼Ð°Ð½Ð´Ð¾Ð¹ /subscribe.",
            reply_markup=back_kb(),
            parse_mode="HTML",
        )
        await cb.answer("ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ Ð°ÐºÑÐ¸Ð²Ð¸ÑÐ¾Ð²Ð°Ð½!")
    else:
        await cb.answer(
            "ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ ÑÐ¶Ðµ Ð±ÑÐ» Ð¸ÑÐ¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°Ð½. ÐÑÐ¾ÑÐ¼Ð¸ÑÐµ Ð¿Ð»Ð°ÑÐ½ÑÑ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÑ.",
            show_alert=True,
        )


@router.message(Command("subscribe"))
async def cmd_subscribe(msg: Message) -> None:
    """ÐÐ¾ÐºÐ°Ð·ÑÐ²Ð°ÐµÑ Ð¼ÐµÐ½Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ¸."""
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
        trial_note = " (Ð¿ÑÐ¾Ð±Ð½ÑÐ¹)" if sub.get("is_trial") else ""
        text = (
            f"â <b>ÐÐ¾Ð´Ð¿Ð¸ÑÐºÐ° Ð°ÐºÑÐ¸Ð²Ð½Ð°{trial_note}</b>\n\n"
            f"Ð¢Ð°ÑÐ¸Ñ: {plan}\n"
            f"ÐÐµÐ¹ÑÑÐ²ÑÐµÑ Ð´Ð¾: <b>{exp}</b> ({sub['days_left']} Ð´Ð½.)\n\n"
            "ÐÐ»Ñ Ð¿ÑÐ¾Ð´Ð»ÐµÐ½Ð¸Ñ Ð¸ÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ÑÐµ ÐºÐ½Ð¾Ð¿ÐºÑ Ð½Ð¸Ð¶Ðµ."
        )
    else:
        text = (
            "ð± <b>ÐÐ¾Ð´Ð¿Ð¸ÑÐºÐ° AnomalyAlert</b>\n\n"
            "ÐÐ¾Ð»ÑÑÐ°Ð¹ÑÐµ Ð°Ð»ÐµÑÑÑ Ð¾Ð± Ð°Ð½Ð¾Ð¼Ð°Ð»Ð¸ÑÑ Ð½Ð° ÐÐ¾ÑÐºÐ¾Ð²ÑÐºÐ¾Ð¹ Ð±Ð¸ÑÐ¶Ðµ.\n\n"
            "ÐÑÐ±ÐµÑÐ¸ÑÐµ ÑÐ¿Ð¾ÑÐ¾Ð± Ð¾Ð¿Ð»Ð°ÑÑ:"
        )

    buttons = []
    if yukassa_btn:
        buttons.append([InlineKeyboardButton(text="ð³ ÐÐ¿Ð»Ð°ÑÐ¸ÑÑ ÐºÐ°ÑÑÐ¾Ð¹ / Ð¡ÐÐ", callback_data="pay_yukassa")])
    buttons.append([InlineKeyboardButton(text="â­ ÐÐ¿Ð»Ð°ÑÐ¸ÑÑ Telegram Stars", callback_data="pay_stars")])
    if not sub:
        buttons.append([InlineKeyboardButton(
            text=f"ð ÐÑÐ¾Ð±Ð½ÑÐ¹ Ð¿ÐµÑÐ¸Ð¾Ð´ ({TRIAL_DAYS} Ð´Ð½ÐµÐ¹)",
            callback_data="trial_yukassa" if yukassa_btn else "trial",
        )])
    buttons.append([InlineKeyboardButton(text="ð ÐÐ»Ð°Ð²Ð½Ð¾Ðµ Ð¼ÐµÐ½Ñ", callback_data="main_menu")])

    await msg.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
