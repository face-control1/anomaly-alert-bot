"""
SQLite-хранилище: пользователи, подписки, лог алертов.
"""
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from config import DB_PATH, SUBSCRIPTION_PERIOD_DAYS, TRIAL_PERIOD_HOURS

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
            type            TEXT NOT NULL,
            started_at      REAL NOT NULL,
            expires_at      REAL NOT NULL,
            payment_id      TEXT,
            stars_amount    INTEGER,
            is_active       INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS alerts_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  REAL NOT NULL,
            ticker      TEXT NOT NULL,
            exchange    TEXT NOT NULL,
            alert_type  TEXT NOT NULL,
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
        """)


def upsert_user(user_id: int, username: str | None, first_name: str | None) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
        """, (user_id, username, first_name, datetime.now(timezone.utc).timestamp()))


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
            "started_at": datetime.fromtimestamp(row["started_at"], tz=timezone.utc),
            "expires_at": datetime.fromtimestamp(row["expires_at"], tz=timezone.utc),
        }
    return None


def had_trial(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM subscriptions WHERE user_id=? AND type='trial' LIMIT 1",
            (user_id,)
        ).fetchone()
    return row is not None


def activate_trial(user_id: int) -> None:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=TRIAL_PERIOD_HOURS)
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO subscriptions (user_id, type, started_at, expires_at)
            VALUES (?, 'trial', ?, ?)
        """, (user_id, now.timestamp(), expires.timestamp()))


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


def log_alert(ticker: str, exchange: str, alert_type: str, details: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO alerts_log (created_at, ticker, exchange, alert_type, details)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now(timezone.utc).timestamp(), ticker, exchange,
              alert_type, details))


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
