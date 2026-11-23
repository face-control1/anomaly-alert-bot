"""
SQLite-хранилище: пользователи, подписки, лог алертов.
"""
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from config import DB_PATH, SUBSCRIPTION_PERIOD_DAYS, TRIAL_PERIOD_HOURS

# Московский часовой пояс (UTC+3)
MSK = timezone(timedelta(hours=3))

os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)


@contextmanager
def get_conn():
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


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            first_name    TEXT,
            joined_at     REAL NOT NULL,
            is_blocked    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            type            TEXT NOT NULL,   -- 'trial' | 'paid'
            started_at      REAL NOT NULL,
            expires_at      REAL NOT NULL,
            payment_id      TEXT,            -- charge_id от Telegram
            stars_amount    INTEGER,
            is_active       INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS alerts_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  REAL NOT NULL,
            ticker      TEXT NOT NULL,
            exchange    TEXT NOT NULL,       -- 'MOEX' | 'SPBEX'
            alert_type  TEXT NOT NULL,       -- 'volume_spike' | 'price_move' | 'volatility'
            details     TEXT
        );

        CREATE TABLE IF NOT EXISTS user_tickers (
            user_id     INTEGER NOT NULL,
            ticker      TEXT NOT NULL,
            exchange    TEXT NOT NULL,
            added_at    REAL NOT NULL,
            PRIMARY KEY (user_id, ticker, exchange),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sub_active ON subscriptions(is_active, expires_at);

        CREATE TABLE IF NOT EXISTS favorites (
            user_id     INTEGER NOT NULL,
            ticker      TEXT NOT NULL,
            exchange    TEXT NOT NULL DEFAULT 'MOEX',
            added_at    REAL NOT NULL,
            PRIMARY KEY (user_id, ticker, exchange),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key     TEXT PRIMARY KEY,
            value   TEXT NOT NULL
        );

        INSERT OR IGNORE INTO settings (key, value) VALUES ('subscription_price_stars', '250');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('trial_hours', '24');
        """)


# ─── Пользователи ────────────────────────────────────────────────────────────

def upsert_user(user_id: int, username: str | None, first_name: str | None) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
        """, (user_id, username, first_name, datetime.now(timezone.utc).timestamp()))


# ─── Подписки ─────────────────────────────────────────────────────────────────

def has_active_subscription(user_id: int) -> bool:
    now = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT 1 FROM subscriptions
            WHERE user_id=? AND is_active=1 AND expires_at>?
            LIMIT 1
        """, (user_id, now)).fetchone()
    return row is not None


def get_subscription_info(user_id: int) -> dict | None:
    now = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT type, started_at, expires_at FROM subscriptions
            WHERE user_id=? AND is_active=1 AND expires_at>?
            ORDER BY expires_at DESC LIMIT 1
        """, (user_id, now)).fetchone()
    if row:
        return {
            "type": row["type"],
            "started_at": datetime.fromtimestamp(row["started_at"], tz=MSK),
            "expires_at": datetime.fromtimestamp(row["expires_at"], tz=MSK),
        }
    return None


def had_trial(user_id: int) -> bool:
    """Вернуть True если пользователь КОГДА-ЛИБО получал триал (включая истёкший)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM subscriptions WHERE user_id=? AND type='trial' LIMIT 1",
            (user_id,)
        ).fetchone()
    return row is not None


def activate_trial(user_id: int) -> bool:
    """Активировать триал. Вернуть False если триал уже был."""
    if had_trial(user_id):
        return False
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=TRIAL_PERIOD_HOURS)
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO subscriptions (user_id, type, started_at, expires_at)
            VALUES (?, 'trial', ?, ?)
        """, (user_id, now.timestamp(), expires.timestamp()))
    return True


def activate_paid(user_id: int, payment_id: str, stars: int) -> None:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SUBSCRIPTION_PERIOD_DAYS)
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO subscriptions (user_id, type, started_at, expires_at,
                                       payment_id, stars_amount)
            VALUES (?, 'paid', ?, ?, ?, ?)
        """, (user_id, now.timestamp(), expires.timestamp(), payment_id, stars))


def deactivate_subscription(payment_id: str) -> None:
    """Вызывается при возврате Stars."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE subscriptions SET is_active=0 WHERE payment_id=?",
            (payment_id,)
        )


def get_all_active_user_ids() -> list[int]:
    now = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT user_id FROM subscriptions
            WHERE is_active=1 AND expires_at>?
        """, (now,)).fetchall()
    return [r["user_id"] for r in rows]


# ─── Персональные тикеры ──────────────────────────────────────────────────────

def add_user_ticker(user_id: int, ticker: str, exchange: str) -> bool:
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO user_tickers (user_id, ticker, exchange, added_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, ticker.upper(), exchange.upper(),
                  datetime.now(timezone.utc).timestamp()))
        return True
    except Exception:
        return False


def remove_user_ticker(user_id: int, ticker: str, exchange: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM user_tickers WHERE user_id=? AND ticker=? AND exchange=?",
            (user_id, ticker.upper(), exchange.upper())
        )


def get_user_tickers(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ticker, exchange FROM user_tickers WHERE user_id=? ORDER BY ticker",
            (user_id,)
        ).fetchall()
    return [{"ticker": r["ticker"], "exchange": r["exchange"]} for r in rows]


# ─── Избранные акции ─────────────────────────────────────────────────────────

def add_favorite(user_id: int, ticker: str, exchange: str = "MOEX") -> bool:
    """Добавить акцию в избранное."""
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO favorites (user_id, ticker, exchange, added_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, ticker.upper(), exchange.upper(),
                  datetime.now(timezone.utc).timestamp()))
        return True
    except Exception:
        return False


def remove_favorite(user_id: int, ticker: str, exchange: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM favorites WHERE user_id=? AND ticker=? AND exchange=?",
            (user_id, ticker.upper(), exchange.upper())
        )


def get_favorites(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ticker, exchange FROM favorites WHERE user_id=? ORDER BY ticker",
            (user_id,)
        ).fetchall()
    return [{"ticker": r["ticker"], "exchange": r["exchange"]} for r in rows]


def get_users_watching_ticker(ticker: str, exchange: str) -> list[int]:
    """Вернуть user_id всех, у кого тикер в избранном (только с активной подпиской)."""
    now = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT f.user_id FROM favorites f
            JOIN subscriptions s ON s.user_id = f.user_id
            WHERE f.ticker=? AND f.exchange=?
              AND s.is_active=1 AND s.expires_at>?
        """, (ticker.upper(), exchange.upper(), now)).fetchall()
    return [r["user_id"] for r in rows]


# ─── Лог алертов ──────────────────────────────────────────────────────────────

def log_alert(ticker: str, exchange: str, alert_type: str, details: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO alerts_log (created_at, ticker, exchange, alert_type, details)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now(timezone.utc).timestamp(), ticker, exchange,
              alert_type, details))


# ─── Настройки ───────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )


def get_subscription_price() -> int:
    return int(get_setting("subscription_price_stars", "250"))


def set_subscription_price(stars: int) -> None:
    set_setting("subscription_price_stars", str(stars))


# ─── Ручная выдача пробного периода ──────────────────────────────────────────

def grant_trial(user_id: int, hours: int = 24) -> None:
    """Выдать пробный период вручную (даже если уже был trial — только для админа)."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=hours)
    with get_conn() as conn:
        # Деактивируем старый trial если есть
        conn.execute(
            "UPDATE subscriptions SET is_active=0 WHERE user_id=? AND type='trial'",
            (user_id,)
        )
        conn.execute("""
            INSERT INTO subscriptions (user_id, type, started_at, expires_at)
            VALUES (?, 'trial', ?, ?)
        """, (user_id, now.timestamp(), expires.timestamp()))


def get_all_users() -> list[dict]:
    """Список всех пользователей для admin панели."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT u.user_id, u.username, u.first_name,
                   s.type as sub_type, s.expires_at
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id = u.user_id
                AND s.is_active=1 AND s.expires_at > ?
            ORDER BY u.joined_at DESC
            LIMIT 100
        """, (datetime.now(timezone.utc).timestamp(),)).fetchall()
    result = []
    for r in rows:
        result.append({
            "user_id": r["user_id"],
            "username": r["username"],
            "first_name": r["first_name"],
            "sub_type": r["sub_type"],
            "expires_at": datetime.fromtimestamp(r["expires_at"], tz=MSK) if r["expires_at"] else None,
        })
    return result


# ─── Статистика (для админа) ──────────────────────────────────────────────────

def get_stats() -> dict:
    now = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_subs = conn.execute("""
            SELECT COUNT(DISTINCT user_id) FROM subscriptions
            WHERE is_active=1 AND expires_at>?
        """, (now,)).fetchone()[0]
        paid_subs = conn.execute("""
            SELECT COUNT(DISTINCT user_id) FROM subscriptions
            WHERE type='paid' AND is_active=1 AND expires_at>?
        """, (now,)).fetchone()[0]
        alerts_today = conn.execute("""
            SELECT COUNT(*) FROM alerts_log WHERE created_at>?
        """, (now - 86400,)).fetchone()[0]
    return {
        "total_users": total_users,
        "active_subs": active_subs,
        "paid_subs": paid_subs,
        "alerts_today": alerts_today,
    }
