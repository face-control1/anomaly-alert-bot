"""
database/db.py â ÑÐ½Ð¸Ð²ÐµÑÑÐ°Ð»ÑÐ½Ð°Ñ Ð¾Ð±ÑÑÑÐºÐ°: PostgreSQL (ÐµÑÐ»Ð¸ DATABASE_URL) Ð¸Ð»Ð¸ SQLite
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# ÐÐ¾ÑÐºÐ¾Ð²ÑÐºÐ¸Ð¹ ÑÐ°ÑÐ¾Ð²Ð¾Ð¹ Ð¿Ð¾ÑÑ (UTC+3)
MSK = timezone(timedelta(hours=3))

DATABASE_URL = os.getenv("DATABASE_URL")
DB_PATH: str = os.getenv("DB_PATH", "data/bot.db")
TRIAL_PERIOD_HOURS = int(os.getenv("TRIAL_PERIOD_HOURS", "24"))

USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras

    @contextmanager
    def get_conn():
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    PH = "%s"  # placeholder Ð´Ð»Ñ PostgreSQL

else:
    import sqlite3

    @contextmanager
    def get_conn():
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    PH = "?"  # placeholder Ð´Ð»Ñ SQLite


def init_db() -> None:
    """Ð¡Ð¾Ð·Ð´Ð°ÑÑ ÑÐ°Ð±Ð»Ð¸ÑÑ ÐµÑÐ»Ð¸ Ð½Ðµ ÑÑÑÐµÑÑÐ²ÑÑÑ."""
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id     BIGINT PRIMARY KEY,
                    username    TEXT,
                    created_at  DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id          SERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL REFERENCES users(user_id),
                    type        TEXT NOT NULL,
                    started_at  DOUBLE PRECISION NOT NULL,
                    expires_at  DOUBLE PRECISION NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tickers (
                    user_id     BIGINT NOT NULL REFERENCES users(user_id),
                    ticker      TEXT NOT NULL,
                    exchange    TEXT NOT NULL DEFAULT 'MOEX',
                    PRIMARY KEY (user_id, ticker, exchange)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id              SERIAL PRIMARY KEY,
                    user_id         BIGINT NOT NULL REFERENCES users(user_id),
                    telegram_charge TEXT UNIQUE,
                    amount          INTEGER NOT NULL,
                    currency        TEXT NOT NULL,
                    created_at      DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alerts_log (
                    id          SERIAL PRIMARY KEY,
                    ticker      TEXT NOT NULL,
                    exchange    TEXT NOT NULL DEFAULT 'MOEX',
                    alert_type  TEXT NOT NULL,
                    headline    TEXT,
                    created_at  DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            cur.close()
        else:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id     INTEGER PRIMARY KEY,
                    username    TEXT,
                    created_at  REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL REFERENCES users(user_id),
                    type        TEXT NOT NULL,
                    started_at  REAL NOT NULL,
                    expires_at  REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tickers (
                    user_id     INTEGER NOT NULL REFERENCES users(user_id),
                    ticker      TEXT NOT NULL,
                    exchange    TEXT NOT NULL DEFAULT 'MOEX',
                    PRIMARY KEY (user_id, ticker, exchange)
                );
                CREATE TABLE IF NOT EXISTS payments (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL REFERENCES users(user_id),
                    telegram_charge TEXT UNIQUE,
                    amount          INTEGER NOT NULL,
                    currency        TEXT NOT NULL,
                    created_at      REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS alerts_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker      TEXT NOT NULL,
                    exchange    TEXT NOT NULL DEFAULT 'MOEX',
                    alert_type  TEXT NOT NULL,
                    headline    TEXT,
                    created_at  REAL NOT NULL DEFAULT (unixepoch())
                );
            """)
    logger.info("DB tables ready (backend: %s)", "PostgreSQL" if USE_POSTGRES else "SQLite")


def ensure_user(user_id: int, username: str | None = None) -> None:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO users (user_id, username) VALUES ({PH}, {PH}) "
                f"ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username",
                (user_id, username)
            )
            cur.close()
        else:
            conn.execute(
                "INSERT INTO users (user_id, username) VALUES (?, ?) "
                "ON CONFLICT (user_id) DO UPDATE SET username = excluded.username",
                (user_id, username)
            )


def had_trial(user_id: int) -> bool:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"SELECT 1 FROM subscriptions WHERE user_id = {PH} AND type = 'trial' LIMIT 1",
                (user_id,)
            )
            result = cur.fetchone() is not None
            cur.close()
            return result
        else:
            row = conn.execute(
                "SELECT 1 FROM subscriptions WHERE user_id = ? AND type = 'trial' LIMIT 1",
                (user_id,)
            ).fetchone()
            return row is not None


def activate_trial(user_id: int) -> bool:
    """ÐÐºÑÐ¸Ð²Ð¸ÑÐ¾Ð²Ð°ÑÑ ÑÑÐ¸Ð°Ð». ÐÐµÑÐ½ÑÑÑ False ÐµÑÐ»Ð¸ ÑÑÐ¸Ð°Ð» ÑÐ¶Ðµ Ð±ÑÐ»."""
    if had_trial(user_id):
        return False
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=TRIAL_PERIOD_HOURS)
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO subscriptions (user_id, type, started_at, expires_at) "
                f"VALUES ({PH}, 'trial', {PH}, {PH})",
                (user_id, now.timestamp(), expires.timestamp())
            )
            cur.close()
        else:
            conn.execute(
                "INSERT INTO subscriptions (user_id, type, started_at, expires_at) "
                "VALUES (?, 'trial', ?, ?)",
                (user_id, now.timestamp(), expires.timestamp())
            )
    return True


def get_subscription_info(user_id: int) -> dict | None:
    """ÐÐµÑÐ½ÑÑÑ Ð°ÐºÑÐ¸Ð²Ð½ÑÑ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÑ Ð¸Ð»Ð¸ None."""
    now_ts = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"SELECT type, started_at, expires_at FROM subscriptions "
                f"WHERE user_id = {PH} AND expires_at > {PH} "
                f"ORDER BY expires_at DESC LIMIT 1",
                (user_id, now_ts)
            )
            row = cur.fetchone()
            cur.close()
        else:
            row = conn.execute(
                "SELECT type, started_at, expires_at FROM subscriptions "
                "WHERE user_id = ? AND expires_at > ? "
                "ORDER BY expires_at DESC LIMIT 1",
                (user_id, now_ts)
            ).fetchone()
    if row is None:
        return None
    return {
        "type": row["type"],
        "started_at": datetime.fromtimestamp(row["started_at"], tz=MSK),
        "expires_at": datetime.fromtimestamp(row["expires_at"], tz=MSK),
    }


def is_subscribed(user_id: int) -> bool:
    return get_subscription_info(user_id) is not None


def add_subscription(user_id: int, months: int = 1) -> None:
    now = datetime.now(timezone.utc)
    info = get_subscription_info(user_id)
    if info:
        base = info["expires_at"].astimezone(timezone.utc)
    else:
        base = now
    expires = base + timedelta(days=30 * months)
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO subscriptions (user_id, type, started_at, expires_at) "
                f"VALUES ({PH}, 'paid', {PH}, {PH})",
                (user_id, now.timestamp(), expires.timestamp())
            )
            cur.close()
        else:
            conn.execute(
                "INSERT INTO subscriptions (user_id, type, started_at, expires_at) "
                "VALUES (?, 'paid', ?, ?)",
                (user_id, now.timestamp(), expires.timestamp())
            )


def get_all_active_user_ids() -> list[int]:
    """ÐÐµÑÐ½ÑÑÑ ÑÐ¿Ð¸ÑÐ¾Ðº user_id Ñ ÐºÐ¾Ð³Ð¾ ÐµÑÑÑ Ð°ÐºÑÐ¸Ð²Ð½Ð°Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ° (Ð´Ð»Ñ ÑÐ°ÑÑÑÐ»ÐºÐ¸ Ð°Ð»ÐµÑÑÐ¾Ð²)."""
    now_ts = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"SELECT DISTINCT user_id FROM subscriptions WHERE expires_at > {PH}",
                (now_ts,)
            )
            rows = cur.fetchall()
            cur.close()
            return [r[0] for r in rows]
        else:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT user_id FROM subscriptions WHERE expires_at > ?",
                (now_ts,)
            ).fetchall()]


def log_alert(ticker: str, exchange: str, alert_type: str, headline: str | None = None) -> None:
    """ÐÐ°Ð¿Ð¸ÑÐ°ÑÑ Ð°Ð»ÐµÑÑ Ð² Ð»Ð¾Ð³ (Ð´Ð»Ñ ÑÑÐ°ÑÐ¸ÑÑÐ¸ÐºÐ¸ /admin)."""
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO alerts_log (ticker, exchange, alert_type, headline) "
                f"VALUES ({PH}, {PH}, {PH}, {PH})",
                (ticker, exchange, alert_type, headline)
            )
            cur.close()
        else:
            conn.execute(
                "INSERT INTO alerts_log (ticker, exchange, alert_type, headline) VALUES (?, ?, ?, ?)",
                (ticker, exchange, alert_type, headline)
            )


def get_favorites(user_id: int) -> list[dict]:
    """ÐÐµÑÐ½ÑÑÑ Ð¸Ð·Ð±ÑÐ°Ð½Ð½ÑÐµ ÑÐ¸ÐºÐµÑÑ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ: [{"ticker": ..., "exchange": ...}]."""
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"SELECT ticker, exchange FROM tickers WHERE user_id = {PH}",
                (user_id,)
            )
            rows = cur.fetchall()
            cur.close()
            return [{"ticker": r[0], "exchange": r[1]} for r in rows]
        else:
            return [
                {"ticker": r[0], "exchange": r[1]}
                for r in conn.execute(
                    "SELECT ticker, exchange FROM tickers WHERE user_id = ?",
                    (user_id,)
                ).fetchall()
            ]


def get_user_tickers(user_id: int) -> list[str]:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"SELECT ticker FROM tickers WHERE user_id = {PH}", (user_id,))
            rows = cur.fetchall()
            cur.close()
            return [r[0] for r in rows]
        else:
            return [r[0] for r in conn.execute(
                "SELECT ticker FROM tickers WHERE user_id = ?", (user_id,)
            ).fetchall()]


def add_ticker(user_id: int, ticker: str, exchange: str = "MOEX") -> None:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO tickers (user_id, ticker, exchange) VALUES ({PH}, {PH}, {PH}) ON CONFLICT DO NOTHING",
                (user_id, ticker.upper(), exchange)
            )
            cur.close()
        else:
            conn.execute(
                "INSERT OR IGNORE INTO tickers (user_id, ticker, exchange) VALUES (?, ?, ?)",
                (user_id, ticker.upper(), exchange)
            )


def remove_ticker(user_id: int, ticker: str, exchange: str = "MOEX") -> None:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM tickers WHERE user_id = {PH} AND ticker = {PH} AND exchange = {PH}",
                (user_id, ticker.upper(), exchange)
            )
            cur.close()
        else:
            conn.execute(
                "DELETE FROM tickers WHERE user_id = ? AND ticker = ? AND exchange = ?",
                (user_id, ticker.upper(), exchange)
            )


def get_all_users() -> list[dict]:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT user_id, username FROM users")
            rows = cur.fetchall()
            cur.close()
            users = [{"user_id": r[0], "username": r[1]} for r in rows]
        else:
            users = [
                {"user_id": r[0], "username": r[1]}
                for r in conn.execute("SELECT user_id, username FROM users").fetchall()
            ]
    result = []
    for u in users:
        sub = get_subscription_info(u["user_id"])
        result.append({
            "user_id": u["user_id"],
            "username": u["username"],
            "subscription": sub,
        })
    return result


def record_payment(user_id: int, telegram_charge: str, amount: int, currency: str) -> None:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO payments (user_id, telegram_charge, amount, currency) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}) ON CONFLICT (telegram_charge) DO NOTHING",
                (user_id, telegram_charge, amount, currency)
            )
            cur.close()
        else:
            conn.execute(
                "INSERT OR IGNORE INTO payments (user_id, telegram_charge, amount, currency) "
                "VALUES (?, ?, ?, ?)",
                (user_id, telegram_charge, amount, currency)
            )


# ───────────────────────────────────────────────
# Алиасы и совместимые функции для handlers.py
# ───────────────────────────────────────────────

def upsert_user(user_id: int, username: str | None = None) -> None:
    """Алиас для ensure_user."""
    ensure_user(user_id, username)


def has_active_subscription(user_id: int) -> bool:
    """Алиас для is_subscribed."""
    return is_subscribed(user_id)


def activate_paid(user_id: int, months: int = 1) -> None:
    """Алиас для add_subscription."""
    add_subscription(user_id, months)


def deactivate_subscription(user_id: int) -> None:
    """Немедленно завершить все активные подписки пользователя."""
    now_ts = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE subscriptions SET expires_at = {PH} WHERE user_id = {PH} AND expires_at > {PH}",
                (now_ts, user_id, now_ts)
            )
            cur.close()
        else:
            conn.execute(
                "UPDATE subscriptions SET expires_at = ? WHERE user_id = ? AND expires_at > ?",
                (now_ts, user_id, now_ts)
            )


def add_user_ticker(user_id: int, ticker: str, exchange: str = "MOEX") -> None:
    """Алиас для add_ticker."""
    add_ticker(user_id, ticker, exchange)


def remove_user_ticker(user_id: int, ticker: str, exchange: str = "MOEX") -> None:
    """Алиас для remove_ticker."""
    remove_ticker(user_id, ticker, exchange)


def get_stats() -> dict:
    """Статистика для /admin: пользователи, подписки, алерты за 24ч."""
    now_ts = datetime.now(timezone.utc).timestamp()
    day_ago = now_ts - 86400
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]
            cur.execute(
                f"SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE expires_at > {PH}",
                (now_ts,)
            )
            active_subs = cur.fetchone()[0]
            cur.execute(
                f"SELECT COUNT(*) FROM alerts_log WHERE created_at > {PH}",
                (day_ago,)
            )
            alerts_24h = cur.fetchone()[0]
            cur.close()
        else:
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            active_subs = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE expires_at > ?",
                (now_ts,)
            ).fetchone()[0]
            alerts_24h = conn.execute(
                "SELECT COUNT(*) FROM alerts_log WHERE created_at > ?",
                (day_ago,)
            ).fetchone()[0]
    return {
        "total_users": total_users,
        "active_subscriptions": active_subs,
        "alerts_24h": alerts_24h,
    }


# Цена подписки хранится в памяти (можно перенести в БД позже)
_subscription_price: dict = {"stars": 250}


def get_subscription_price() -> dict:
    """Вернуть текущую цену подписки."""
    return dict(_subscription_price)


def set_subscription_price(stars: int) -> None:
    """Установить цену подписки."""
    _subscription_price["stars"] = stars


def grant_trial(user_id: int) -> bool:
    """Принудительно дать триал (даже если уже был). Для /admin."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=TRIAL_PERIOD_HOURS)
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO subscriptions (user_id, type, started_at, expires_at) "
                f"VALUES ({PH}, 'trial', {PH}, {PH})",
                (user_id, now.timestamp(), expires.timestamp())
            )
            cur.close()
        else:
            conn.execute(
                "INSERT INTO subscriptions (user_id, type, started_at, expires_at) VALUES (?, 'trial', ?, ?)",
                (user_id, now.timestamp(), expires.timestamp())
            )
    return True


def add_favorite(user_id: int, ticker: str, exchange: str = "MOEX") -> None:
    """Алиас для add_ticker."""
    add_ticker(user_id, ticker, exchange)


def remove_favorite(user_id: int, ticker: str, exchange: str = "MOEX") -> None:
    """Алиас для remove_ticker."""
    remove_ticker(user_id, ticker, exchange)
