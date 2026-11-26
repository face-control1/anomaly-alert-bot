"""
Оплата через ЮKassa (карты + СБП).
Требует: yookassa, apscheduler, sqlalchemy

Env vars:
  YUKASSA_SHOP_ID   — идентификатор магазина
  YUKASSA_SECRET    — секретный ключ
  WEBHOOK_SECRET    — секрет для верификации вебхука (опционально)
"""
import os
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database.db import get_ruble_plans

logger = logging.getLogger(__name__)

# --- Конфигурация ---
YUKASSA_SHOP_ID: str = os.getenv("YUKASSA_SHOP_ID", "")
YUKASSA_SECRET: str = os.getenv("YUKASSA_SECRET", "")
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")

YUKASSA_AVAILABLE = bool(YUKASSA_SHOP_ID and YUKASSA_SECRET)

TRIAL_DAYS = 1

# --- DB helpers (SQLAlchemy / SQLite) ---
try:
    from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text
    from sqlalchemy.orm import declarative_base, sessionmaker

    DB_URL = os.getenv("DATABASE_URL", "sqlite:///subscriptions.db")
    if DB_URL.startswith("postgres://"):
        DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

    engine = create_engine(DB_URL, echo=False, connect_args={"check_same_thread": False} if "sqlite" in DB_URL else {})
    Base = declarative_base()
    Session = sessionmaker(bind=engine)

    class Subscription(Base):
        __tablename__ = "subscriptions"
        id = Column(Integer, primary_key=True, autoincrement=True)
        user_id = Column(Integer, index=True, nullable=False)
        plan = Column(String(32), nullable=False)
        expires_at = Column(DateTime, nullable=False)
        is_trial = Column(Boolean, default=False)
        created_at = Column(DateTime, default=datetime.utcnow)

    class PaymentRecord(Base):
        __tablename__ = "payment_history"
        id = Column(Integer, primary_key=True, autoincrement=True)
        user_id = Column(Integer, index=True, nullable=False)
        payment_id = Column(String(64), unique=True, nullable=False)
        plan = Column(String(32), nullable=False)
        amount = Column(Integer, nullable=False)
        status = Column(String(32), default="pending")
        created_at = Column(DateTime, default=datetime.utcnow)
        confirmed_at = Column(DateTime, nullable=True)
        raw = Column(Text, nullable=True)

    Base.metadata.create_all(engine)
    DB_OK = True

except Exception as e:
    logger.warning("payments_yukassa: DB init failed: %s", e)
    DB_OK = False


# --- ЮKassa API ---
def _yukassa_headers() -> dict:
    import base64
    creds = base64.b64encode(f"{YUKASSA_SHOP_ID}:{YUKASSA_SECRET}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "Idempotence-Key": str(uuid.uuid4()),
    }


async def create_payment(user_id: int, plan_key: str, return_url: str) -> Optional[dict]:
    """Создаёт платёж в ЮKassa, возвращает {"payment_id": ..., "confirmation_url": ...}"""
    if not YUKASSA_AVAILABLE:
        logger.error("ЮKassa не настроена (YUKASSA_SHOP_ID/YUKASSA_SECRET не заданы)")
        return None

    plan = next((item for item in get_ruble_plans() if item["key"] == plan_key), None)
    if plan:
        plan = {**plan, "description": f"Подписка {plan['label']}"}
    if not plan:
        return None

    import aiohttp

    payload = {
        "amount": {"value": str(plan["price"]) + ".00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": f"[user:{user_id}] {plan['description']}",
        "metadata": {"user_id": str(user_id), "plan": plan_key},
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.yookassa.ru/v3/payments",
                json=payload,
                headers=_yukassa_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status == 200:
                    payment_id = data["id"]
                    conf_url = data.get("confirmation", {}).get("confirmation_url", "")
                    _record_payment(user_id, payment_id, plan_key, plan["price"])
                    return {"payment_id": payment_id, "confirmation_url": conf_url}
                else:
                    logger.error("ЮKassa create_payment error %d: %s", resp.status, data)
    except Exception as e:
        logger.error("ЮKassa create_payment exception: %s", e)
    return None


def _record_payment(user_id: int, payment_id: str, plan: str, amount: int):
    if not DB_OK:
        return
    with Session() as db:
        db.add(PaymentRecord(user_id=user_id, payment_id=payment_id, plan=plan, amount=amount))
        db.commit()


def confirm_payment(payment_id: str, raw_event: dict) -> Optional[int]:
    """
    Обрабатывает событие payment.succeeded.
    Возвращает user_id если подписка успешно активирована.
    """
    if not DB_OK:
        return None

    metadata = raw_event.get("object", {}).get("metadata", {})
    user_id_str = metadata.get("user_id")
    plan_key = metadata.get("plan")

    if not user_id_str or not plan_key:
        logger.warning("confirm_payment: нет metadata в событии")
        return None

    user_id = int(user_id_str)
    plan = next((item for item in get_ruble_plans() if item["key"] == plan_key), None)
    if not plan:
        return None

    with Session() as db:
        # Обновляем запись платежа
        pr = db.query(PaymentRecord).filter_by(payment_id=payment_id).first()
        if pr:
            pr.status = "succeeded"
            pr.confirmed_at = datetime.utcnow()
            pr.raw = json.dumps(raw_event, ensure_ascii=False)[:2000]

        # Продлеваем / создаём подписку
        sub = db.query(Subscription).filter_by(user_id=user_id).first()
        now = datetime.utcnow()
        if sub and sub.expires_at > now:
            # Продлеваем от текущей даты истечения
            sub.expires_at = sub.expires_at + timedelta(days=plan["days"])
        else:
            if sub:
                sub.expires_at = now + timedelta(days=plan["days"])
                sub.plan = plan_key
                sub.is_trial = False
                sub.created_at = now
            else:
                db.add(Subscription(
                    user_id=user_id,
                    plan=plan_key,
                    expires_at=now + timedelta(days=plan["days"]),
                    is_trial=False,
                ))
        db.commit()

    logger.info("Подписка активирована: user_id=%d plan=%s", user_id, plan_key)
    return user_id


def activate_trial(user_id: int) -> bool:
    """Активирует пробный период. Возвращает False если уже использован."""
    if not DB_OK:
        return False
    with Session() as db:
        existing = db.query(Subscription).filter_by(user_id=user_id).first()
        if existing:
            return False  # уже есть подписка или триал
        # Проверяем историю платежей
        any_payment = db.query(PaymentRecord).filter_by(user_id=user_id).first()
        if any_payment:
            return False
        db.add(Subscription(
            user_id=user_id,
            plan="trial",
            expires_at=datetime.utcnow() + timedelta(days=TRIAL_DAYS),
            is_trial=True,
        ))
        db.commit()
    return True


def get_subscription(user_id: int) -> Optional[dict]:
    """Возвращает dict с info о подписке или None."""
    if not DB_OK:
        return None
    with Session() as db:
        sub = db.query(Subscription).filter_by(user_id=user_id).first()
        if not sub:
            return None
        return {
            "plan": sub.plan,
            "expires_at": sub.expires_at,
            "is_trial": sub.is_trial,
            "is_active": sub.expires_at > datetime.utcnow(),
            "days_left": (sub.expires_at - datetime.utcnow()).days,
        }


def is_subscribed(user_id: int) -> bool:
    sub = get_subscription(user_id)
    return sub is not None and sub["is_active"]


# --- Вебхук верификация ---
def verify_webhook(body: bytes, signature: str) -> bool:
    """Проверяет подпись вебхука ЮKassa (SHA-256 HMAC)."""
    if not WEBHOOK_SECRET:
        return True  # если секрет не задан — не проверяем
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# --- APScheduler напоминания ---
def setup_expiry_reminders(scheduler: AsyncIOScheduler, bot: Bot):
    """Планирует ежедневную проверку и рассылку напоминаний об истечении подписки."""

    async def _send_reminders():
        if not DB_OK:
            return
        now = datetime.utcnow()
        reminders = [1, 2, 3]  # за сколько дней до конца слать
        with Session() as db:
            subs = db.query(Subscription).filter(Subscription.expires_at > now).all()
            for sub in subs:
                days_left = (sub.expires_at - now).days
                if days_left in reminders:
                    plan_info = next((item for item in get_ruble_plans() if item["key"] == sub.plan), {})
                    plan_label = plan_info.get("label", sub.plan)
                    try:
                        await bot.send_message(
                            sub.user_id,
                            f"⏰ <b>Напоминание</b>\n\n"
                            f"Ваша подписка <b>{plan_label}</b> истекает через "
                            f"<b>{days_left} {'день' if days_left == 1 else 'дня'}</b>.\n\n"
                            f"Продлите подписку, чтобы не потерять доступ к алертам. "
                            f"Используйте /subscribe для продления.",
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        logger.debug("Reminder send error user %d: %s", sub.user_id, e)

    # Запускаем проверку ежедневно в 9:00 UTC
    scheduler.add_job(_send_reminders, "cron", hour=9, minute=0, id="expiry_reminders", replace_existing=True)
    logger.info("Expiry reminders scheduled")
