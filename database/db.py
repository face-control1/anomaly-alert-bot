"""
database/db.py ÃÂ¢ÃÂÃÂ ÃÂÃÂÃÂÃÂ½ÃÂÃÂ¸ÃÂÃÂ²ÃÂÃÂµÃÂÃÂÃÂÃÂÃÂÃÂ°ÃÂÃÂ»ÃÂÃÂÃÂÃÂ½ÃÂÃÂ°ÃÂÃÂ ÃÂÃÂ¾ÃÂÃÂ±ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂºÃÂÃÂ°: PostgreSQL (ÃÂÃÂµÃÂÃÂÃÂÃÂ»ÃÂÃÂ¸ DATABASE_URL) ÃÂÃÂ¸ÃÂÃÂ»ÃÂÃÂ¸ SQLite
"""

import os
import logging
import shutil
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂºÃÂÃÂ¾ÃÂÃÂ²ÃÂÃÂÃÂÃÂºÃÂÃÂ¸ÃÂÃÂ¹ ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ¾ÃÂÃÂ²ÃÂÃÂ¾ÃÂÃÂ¹ ÃÂÃÂ¿ÃÂÃÂ¾ÃÂÃÂÃÂÃÂ (UTC+3)
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

    PH = "%s"  # placeholder ÃÂÃÂ´ÃÂÃÂ»ÃÂÃÂ PostgreSQL

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

    PH = "?"  # placeholder ÃÂÃÂ´ÃÂÃÂ»ÃÂÃÂ SQLite


def backup_sqlite() -> str | None:
    """Create a recoverable daily SQLite copy; PostgreSQL is backed up by Render."""
    if USE_POSTGRES or not os.path.exists(DB_PATH):
        return None
    backup_dir = os.path.join(os.path.dirname(DB_PATH) or ".", "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    target = os.path.join(backup_dir, f"bot-{stamp}.sqlite")
    shutil.copy2(DB_PATH, target)
    return target


def init_db() -> None:
    """ÃÂÃÂ¡ÃÂÃÂ¾ÃÂÃÂ·ÃÂÃÂ´ÃÂÃÂ°ÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂ°ÃÂÃÂ±ÃÂÃÂ»ÃÂÃÂ¸ÃÂÃÂÃÂÃÂ ÃÂÃÂµÃÂÃÂÃÂÃÂ»ÃÂÃÂ¸ ÃÂÃÂ½ÃÂÃÂµ ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂÃÂÃÂ²ÃÂÃÂÃÂÃÂÃÂÃÂ."""
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id     BIGINT PRIMARY KEY,
                    username    TEXT,
                    first_name  TEXT,
                    created_at  DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
                    referrer_id BIGINT
                )
            """)
            try:
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT")
            except Exception:
                pass
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT")
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
                    beneficiary_user_id BIGINT,
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
                    created_at  DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
                    signal_price DOUBLE PRECISION,
                    direction INTEGER NOT NULL DEFAULT 0,
                    outcome_15m_pct DOUBLE PRECISION,
                    outcome_5m_pct DOUBLE PRECISION,
                    outcome_30m_pct DOUBLE PRECISION,
                    outcome_60m_pct DOUBLE PRECISION,
                    score INTEGER NOT NULL DEFAULT 50,
                    evaluated_at DOUBLE PRECISION
                )
            """)
            cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS beneficiary_user_id BIGINT")
            for statement in (
                "ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS signal_price DOUBLE PRECISION",
                "ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS direction INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS outcome_15m_pct DOUBLE PRECISION",
                "ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS outcome_5m_pct DOUBLE PRECISION",
                "ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS outcome_30m_pct DOUBLE PRECISION",
                "ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS outcome_60m_pct DOUBLE PRECISION",
                "ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS score INTEGER NOT NULL DEFAULT 50",
                "ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS evaluated_at DOUBLE PRECISION",
            ):
                cur.execute(statement)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id       BIGINT PRIMARY KEY REFERENCES users(user_id),
                    volume_alerts BOOLEAN NOT NULL DEFAULT TRUE,
                    price_alerts  BOOLEAN NOT NULL DEFAULT TRUE,
                    band_alerts   BOOLEAN NOT NULL DEFAULT TRUE,
                    quiet_hours   BOOLEAN NOT NULL DEFAULT FALSE,
                    paused        BOOLEAN NOT NULL DEFAULT FALSE
                    ,min_score    INTEGER NOT NULL DEFAULT 60
                    ,direction    TEXT NOT NULL DEFAULT 'all'
                )
            """)
            cur.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS min_score INTEGER NOT NULL DEFAULT 60")
            cur.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS direction TEXT NOT NULL DEFAULT 'all'")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    discount INTEGER NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admin_login_codes (
                    code_hash TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL,
                    used_at DOUBLE PRECISION
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            cur.execute("ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS username TEXT")
            cur.execute("ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS role TEXT")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admin_staff (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'support',
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id SERIAL PRIMARY KEY,
                    admin_id BIGINT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT,
                    created_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            cur.close()
        else:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id     INTEGER PRIMARY KEY,
                    username    TEXT,
                    first_name  TEXT,
                    referrer_id INTEGER,
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
                    beneficiary_user_id INTEGER,
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
                    created_at  REAL NOT NULL DEFAULT (unixepoch()),
                    signal_price REAL,
                    direction INTEGER NOT NULL DEFAULT 0,
                    outcome_15m_pct REAL,
                    outcome_5m_pct REAL,
                    outcome_30m_pct REAL,
                    outcome_60m_pct REAL,
                    score INTEGER NOT NULL DEFAULT 50,
                    evaluated_at REAL
                );
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id       INTEGER PRIMARY KEY REFERENCES users(user_id),
                    volume_alerts INTEGER NOT NULL DEFAULT 1,
                    price_alerts  INTEGER NOT NULL DEFAULT 1,
                    band_alerts   INTEGER NOT NULL DEFAULT 1,
                    quiet_hours   INTEGER NOT NULL DEFAULT 0,
                    paused        INTEGER NOT NULL DEFAULT 0
                    ,min_score    INTEGER NOT NULL DEFAULT 60
                    ,direction    TEXT NOT NULL DEFAULT 'all'
                );
                CREATE TABLE IF NOT EXISTS app_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    discount INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS admin_login_codes (
                    code_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    used_at REAL
                );
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS admin_staff (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'support',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT,
                    created_at REAL NOT NULL DEFAULT (unixepoch())
                );
            """)
            existing_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(alerts_log)").fetchall()
            }
            for name, definition in (
                ("signal_price", "REAL"),
                ("direction", "INTEGER NOT NULL DEFAULT 0"),
                ("outcome_15m_pct", "REAL"),
                ("outcome_5m_pct", "REAL"),
                ("outcome_30m_pct", "REAL"),
                ("outcome_60m_pct", "REAL"),
                ("score", "INTEGER NOT NULL DEFAULT 50"),
                ("evaluated_at", "REAL"),
            ):
                if name not in existing_columns:
                    conn.execute(f"ALTER TABLE alerts_log ADD COLUMN {name} {definition}")
            user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "first_name" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
            if "referrer_id" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER")
            payment_columns = {row[1] for row in conn.execute("PRAGMA table_info(payments)").fetchall()}
            if "beneficiary_user_id" not in payment_columns:
                conn.execute("ALTER TABLE payments ADD COLUMN beneficiary_user_id INTEGER")
            setting_columns = {row[1] for row in conn.execute("PRAGMA table_info(user_settings)").fetchall()}
            if "min_score" not in setting_columns:
                conn.execute("ALTER TABLE user_settings ADD COLUMN min_score INTEGER NOT NULL DEFAULT 60")
            if "direction" not in setting_columns:
                conn.execute("ALTER TABLE user_settings ADD COLUMN direction TEXT NOT NULL DEFAULT 'all'")
            session_columns = {row[1] for row in conn.execute("PRAGMA table_info(admin_sessions)").fetchall()}
            if "username" not in session_columns:
                conn.execute("ALTER TABLE admin_sessions ADD COLUMN username TEXT")
            if "role" not in session_columns:
                conn.execute("ALTER TABLE admin_sessions ADD COLUMN role TEXT")
    logger.info("DB tables ready (backend: %s)", "PostgreSQL" if USE_POSTGRES else "SQLite")


def ensure_user(user_id: int, username: str | None = None, first_name: str | None = None) -> None:
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
                "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?) "
                "ON CONFLICT (user_id) DO UPDATE SET username = excluded.username, first_name = excluded.first_name",
                (user_id, username, first_name)
            )


def register_referral(user_id: int, referrer_id: int) -> bool:
    if user_id == referrer_id:
        return False
    ensure_user(referrer_id)
    ensure_user(user_id)
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE users SET referrer_id = {PH} "
                f"WHERE user_id = {PH} AND referrer_id IS NULL",
                (referrer_id, user_id),
            )
            updated = cur.rowcount == 1
            cur.close()
            return updated
        cur = conn.execute(
            "UPDATE users SET referrer_id = ? WHERE user_id = ? AND referrer_id IS NULL",
            (referrer_id, user_id),
        )
        return cur.rowcount == 1


def get_referral_count(user_id: int) -> int:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM users WHERE referrer_id = {PH}", (user_id,))
            count = cur.fetchone()[0]
            cur.close()
            return int(count)
        return int(conn.execute(
            "SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,)
        ).fetchone()[0])


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
    """Activate the 24-hour trial once, safely under concurrent clicks."""
    ensure_user(user_id)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=TRIAL_PERIOD_HOURS)
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO subscriptions (user_id, type, started_at, expires_at) "
                f"SELECT {PH}, 'trial', {PH}, {PH} "
                f"WHERE NOT EXISTS ("
                f"SELECT 1 FROM subscriptions WHERE user_id = {PH} AND type = 'trial'"
                f") RETURNING id",
                (user_id, now.timestamp(), expires.timestamp(), user_id),
            )
            activated = cur.fetchone() is not None
            cur.close()
        else:
            cur = conn.execute(
                "INSERT INTO subscriptions (user_id, type, started_at, expires_at) "
                "SELECT ?, 'trial', ?, ? "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM subscriptions WHERE user_id = ? AND type = 'trial'"
                ")",
                (user_id, now.timestamp(), expires.timestamp(), user_id),
            )
            activated = cur.rowcount == 1
    return activated


def get_subscription_info(user_id: int) -> dict | None:
    """ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂÃÂÃÂ ÃÂÃÂ°ÃÂÃÂºÃÂÃÂÃÂÃÂ¸ÃÂÃÂ²ÃÂÃÂ½ÃÂÃÂÃÂÃÂ ÃÂÃÂ¿ÃÂÃÂ¾ÃÂÃÂ´ÃÂÃÂ¿ÃÂÃÂ¸ÃÂÃÂÃÂÃÂºÃÂÃÂ ÃÂÃÂ¸ÃÂÃÂ»ÃÂÃÂ¸ None."""
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
    """ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂ¿ÃÂÃÂ¸ÃÂÃÂÃÂÃÂ¾ÃÂÃÂº user_id ÃÂÃÂ ÃÂÃÂºÃÂÃÂ¾ÃÂÃÂ³ÃÂÃÂ¾ ÃÂÃÂµÃÂÃÂÃÂÃÂÃÂÃÂ ÃÂÃÂ°ÃÂÃÂºÃÂÃÂÃÂÃÂ¸ÃÂÃÂ²ÃÂÃÂ½ÃÂÃÂ°ÃÂÃÂ ÃÂÃÂ¿ÃÂÃÂ¾ÃÂÃÂ´ÃÂÃÂ¿ÃÂÃÂ¸ÃÂÃÂÃÂÃÂºÃÂÃÂ° (ÃÂÃÂ´ÃÂÃÂ»ÃÂÃÂ ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ»ÃÂÃÂºÃÂÃÂ¸ ÃÂÃÂ°ÃÂÃÂ»ÃÂÃÂµÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂ²)."""
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


def get_expiring_subscriptions(min_hours: int, max_hours: int) -> list[dict]:
    now_ts = datetime.now(timezone.utc).timestamp()
    low = now_ts + min_hours * 3600
    high = now_ts + max_hours * 3600
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"SELECT user_id, MAX(expires_at) AS expires_at FROM subscriptions "
                f"GROUP BY user_id HAVING MAX(expires_at) >= {PH} AND MAX(expires_at) < {PH}",
                (low, high),
            )
            rows = cur.fetchall()
            cur.close()
        else:
            rows = conn.execute(
                "SELECT user_id, MAX(expires_at) AS expires_at FROM subscriptions "
                "GROUP BY user_id HAVING MAX(expires_at) >= ? AND MAX(expires_at) < ?",
                (low, high),
            ).fetchall()
    return [dict(row) for row in rows]


def log_alert(
    ticker: str,
    exchange: str,
    alert_type: str,
    headline: str | None = None,
    signal_price: float | None = None,
    direction: int = 0,
    score: int = 50,
) -> None:
    """ÃÂÃÂÃÂÃÂ°ÃÂÃÂ¿ÃÂÃÂ¸ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ ÃÂÃÂ°ÃÂÃÂ»ÃÂÃÂµÃÂÃÂÃÂÃÂ ÃÂÃÂ² ÃÂÃÂ»ÃÂÃÂ¾ÃÂÃÂ³ (ÃÂÃÂ´ÃÂÃÂ»ÃÂÃÂ ÃÂÃÂÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂÃÂÃÂ¸ÃÂÃÂºÃÂÃÂ¸ /admin)."""
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO alerts_log (ticker, exchange, alert_type, headline, signal_price, direction, score) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
                (ticker, exchange, alert_type, headline, signal_price, direction, score)
            )
            cur.close()
        else:
            conn.execute(
                "INSERT INTO alerts_log (ticker, exchange, alert_type, headline, signal_price, direction, score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, exchange, alert_type, headline, signal_price, direction, score)
            )


def get_pending_outcomes(horizon_minutes: int = 15, limit: int = 100) -> list[dict]:
    if horizon_minutes not in (5, 15, 30, 60):
        raise ValueError("Unsupported outcome horizon")
    column = f"outcome_{horizon_minutes}m_pct"
    cutoff = datetime.now(timezone.utc).timestamp() - horizon_minutes * 60
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"SELECT id, ticker, signal_price, direction FROM alerts_log "
                f"WHERE created_at <= {PH} AND signal_price > 0 AND {column} IS NULL "
                f"ORDER BY created_at LIMIT {PH}",
                (cutoff, limit),
            )
            rows = cur.fetchall()
            cur.close()
        else:
            rows = conn.execute(
                "SELECT id, ticker, signal_price, direction FROM alerts_log "
                f"WHERE created_at <= ? AND signal_price > 0 AND {column} IS NULL "
                "ORDER BY created_at LIMIT ?",
                (cutoff, limit),
            ).fetchall()
    return [dict(row) for row in rows]


def save_alert_outcome(alert_id: int, outcome_pct: float, horizon_minutes: int = 15) -> None:
    if horizon_minutes not in (5, 15, 30, 60):
        raise ValueError("Unsupported outcome horizon")
    column = f"outcome_{horizon_minutes}m_pct"
    now_ts = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE alerts_log SET {column} = {PH}, evaluated_at = {PH} WHERE id = {PH}",
                (outcome_pct, now_ts, alert_id),
            )
            cur.close()
        else:
            conn.execute(
                f"UPDATE alerts_log SET {column} = ?, evaluated_at = ? WHERE id = ?",
                (outcome_pct, now_ts, alert_id),
            )


def get_signal_performance() -> dict:
    with get_conn() as conn:
        query = (
            "SELECT outcome_15m_pct, direction FROM alerts_log "
            "WHERE outcome_15m_pct IS NOT NULL ORDER BY evaluated_at DESC LIMIT 500"
        )
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(query)
            rows = cur.fetchall()
            cur.close()
        else:
            rows = conn.execute(query).fetchall()
    if not rows:
        return {"evaluated": 0, "confirmed": 0, "confirmation_rate": 0.0, "avg_move": 0.0, "horizons": _get_outcome_horizons()}
    confirmed = 0
    moves = []
    for outcome, direction in rows:
        outcome = float(outcome)
        moves.append(outcome)
        if (direction > 0 and outcome > 0) or (direction < 0 and outcome < 0):
            confirmed += 1
        elif direction == 0 and abs(outcome) >= 0.5:
            confirmed += 1
    return {
        "evaluated": len(rows),
        "confirmed": confirmed,
        "confirmation_rate": round(confirmed / len(rows) * 100, 1),
        "avg_move": round(sum(abs(x) for x in moves) / len(moves), 2),
        "horizons": _get_outcome_horizons(),
    }


def _get_outcome_horizons() -> dict:
    result = {}
    with get_conn() as conn:
        for minutes in (5, 15, 30, 60):
            column = f"outcome_{minutes}m_pct"
            query = f"SELECT {column}, direction FROM alerts_log WHERE {column} IS NOT NULL ORDER BY created_at DESC LIMIT 500"
            if USE_POSTGRES:
                cur = conn.cursor()
                cur.execute(query)
                rows = cur.fetchall()
                cur.close()
            else:
                rows = conn.execute(query).fetchall()
            if not rows:
                result[minutes] = {"count": 0, "rate": 0.0, "avg": 0.0}
                continue
            moves = [float(row[0]) for row in rows]
            confirmed = sum(
                1 for move, row in zip(moves, rows)
                if (int(row[1]) > 0 and move > 0) or (int(row[1]) < 0 and move < 0) or (int(row[1]) == 0 and abs(move) >= 0.5)
            )
            result[minutes] = {
                "count": len(rows),
                "rate": round(confirmed / len(rows) * 100, 1),
                "avg": round(sum(abs(move) for move in moves) / len(moves), 2),
            }
    return result


def get_recent_alerts(limit: int = 10) -> list[dict]:
    limit = max(1, min(int(limit), 25))
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT ticker, exchange, alert_type, headline, created_at "
                "FROM alerts_log ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
            cur.close()
        else:
            rows = conn.execute(
                "SELECT ticker, exchange, alert_type, headline, created_at "
                "FROM alerts_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_favorites(user_id: int) -> list[dict]:
    """ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂÃÂÃÂ ÃÂÃÂ¸ÃÂÃÂ·ÃÂÃÂ±ÃÂÃÂÃÂÃÂ°ÃÂÃÂ½ÃÂÃÂ½ÃÂÃÂÃÂÃÂµ ÃÂÃÂÃÂÃÂ¸ÃÂÃÂºÃÂÃÂµÃÂÃÂÃÂÃÂ ÃÂÃÂ¿ÃÂÃÂ¾ÃÂÃÂ»ÃÂÃÂÃÂÃÂ·ÃÂÃÂ¾ÃÂÃÂ²ÃÂÃÂ°ÃÂÃÂÃÂÃÂµÃÂÃÂ»ÃÂÃÂ: [{"ticker": ..., "exchange": ...}]."""
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


def add_ticker(user_id: int, ticker: str, exchange: str = "MOEX") -> bool:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO tickers (user_id, ticker, exchange) VALUES ({PH}, {PH}, {PH}) ON CONFLICT DO NOTHING",
                (user_id, ticker.upper(), exchange)
            )
            inserted = cur.rowcount == 1
            cur.close()
            return inserted
        else:
            cur = conn.execute(
                "INSERT OR IGNORE INTO tickers (user_id, ticker, exchange) VALUES (?, ?, ?)",
                (user_id, ticker.upper(), exchange)
            )
            return cur.rowcount == 1


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


def record_payment(user_id: int, telegram_charge: str, amount: int, currency: str, beneficiary_user_id: int | None = None) -> bool:
    """Записать платёж один раз. Возвращает True только для нового charge id."""
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO payments (user_id, beneficiary_user_id, telegram_charge, amount, currency) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}) ON CONFLICT (telegram_charge) DO NOTHING",
                (user_id, beneficiary_user_id or user_id, telegram_charge, amount, currency)
            )
            inserted = cur.rowcount == 1
            cur.close()
            return inserted
        else:
            cur = conn.execute(
                "INSERT OR IGNORE INTO payments (user_id, beneficiary_user_id, telegram_charge, amount, currency) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, beneficiary_user_id or user_id, telegram_charge, amount, currency)
            )
            return cur.rowcount == 1


def get_payment_user_id(telegram_charge: str) -> int | None:
    """Найти владельца платежа для корректной обработки возврата Stars."""
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"SELECT user_id FROM payments WHERE telegram_charge = {PH} LIMIT 1",
                (telegram_charge,),
            )
            row = cur.fetchone()
            cur.close()
        else:
            row = conn.execute(
                "SELECT user_id FROM payments WHERE telegram_charge = ? LIMIT 1",
                (telegram_charge,),
            ).fetchone()
    return int(row[0]) if row else None


def get_payment_beneficiary_id(telegram_charge: str) -> int | None:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"SELECT COALESCE(beneficiary_user_id, user_id) FROM payments WHERE telegram_charge = {PH} LIMIT 1",
                (telegram_charge,),
            )
            row = cur.fetchone()
            cur.close()
        else:
            row = conn.execute(
                "SELECT COALESCE(beneficiary_user_id, user_id) FROM payments WHERE telegram_charge = ? LIMIT 1",
                (telegram_charge,),
            ).fetchone()
    return int(row[0]) if row else None


def get_payment_history(user_id: int, limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"SELECT amount, currency, created_at FROM payments WHERE user_id = {PH} ORDER BY created_at DESC LIMIT {PH}",
                (user_id, limit),
            )
            rows = cur.fetchall()
            cur.close()
        else:
            rows = conn.execute(
                "SELECT amount, currency, created_at FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
    return [dict(row) for row in rows]


def get_user_settings(user_id: int) -> dict:
    boolean_defaults = {
        "volume_alerts": True,
        "price_alerts": True,
        "band_alerts": True,
        "quiet_hours": False,
        "paused": False,
    }
    defaults = {**boolean_defaults, "min_score": 60, "direction": "all"}
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT * FROM user_settings WHERE user_id = {PH}", (user_id,))
            row = cur.fetchone()
            cur.close()
        else:
            row = conn.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return defaults
    result = {key: bool(row[key]) for key in boolean_defaults}
    result["min_score"] = int(row["min_score"] or 60)
    result["direction"] = str(row["direction"] or "all")
    return result


def toggle_user_setting(user_id: int, setting: str) -> dict:
    allowed = {"volume_alerts", "price_alerts", "band_alerts", "quiet_hours", "paused"}
    if setting not in allowed:
        raise ValueError("Unknown user setting")
    ensure_user(user_id)
    current = get_user_settings(user_id)
    new_value = not current[setting]
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO user_settings (user_id, {setting}) VALUES ({PH}, {PH}) "
                f"ON CONFLICT (user_id) DO UPDATE SET {setting} = EXCLUDED.{setting}",
                (user_id, new_value),
            )
            cur.close()
        else:
            conn.execute(
                f"INSERT INTO user_settings (user_id, {setting}) VALUES (?, ?) "
                f"ON CONFLICT (user_id) DO UPDATE SET {setting} = excluded.{setting}",
                (user_id, int(new_value)),
            )
    return get_user_settings(user_id)


def cycle_user_preference(user_id: int, preference: str) -> dict:
    """Cycle score threshold or signal direction and return fresh settings."""
    ensure_user(user_id)
    current = get_user_settings(user_id)
    if preference == "min_score":
        values = (40, 60, 80)
        value = values[(values.index(current["min_score"]) + 1) % len(values)] if current["min_score"] in values else 60
    elif preference == "direction":
        values = ("all", "up", "down")
        value = values[(values.index(current["direction"]) + 1) % len(values)] if current["direction"] in values else "all"
    else:
        raise ValueError("Unknown preference")
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO user_settings (user_id, {preference}) VALUES ({PH}, {PH}) "
                f"ON CONFLICT (user_id) DO UPDATE SET {preference} = EXCLUDED.{preference}",
                (user_id, value),
            )
            cur.close()
        else:
            conn.execute(
                f"INSERT INTO user_settings (user_id, {preference}) VALUES (?, ?) "
                f"ON CONFLICT (user_id) DO UPDATE SET {preference} = excluded.{preference}",
                (user_id, value),
            )
    return get_user_settings(user_id)


# Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
# ÃÂÃÂ»ÃÂ¸ÃÂ°ÃÂÃÂ ÃÂ¸ ÃÂÃÂ¾ÃÂ²ÃÂ¼ÃÂµÃÂÃÂÃÂ¸ÃÂ¼ÃÂÃÂµ ÃÂÃÂÃÂ½ÃÂºÃÂÃÂ¸ÃÂ¸ ÃÂ´ÃÂ»ÃÂ handlers.py
# Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ

def upsert_user(user_id: int, username: str | None = None, first_name: str | None = None) -> None:
    """ÃÂÃÂ»ÃÂ¸ÃÂ°ÃÂ ÃÂ´ÃÂ»ÃÂ ensure_user."""
    ensure_user(user_id, username, first_name)


def has_active_subscription(user_id: int) -> bool:
    """ÃÂÃÂ»ÃÂ¸ÃÂ°ÃÂ ÃÂ´ÃÂ»ÃÂ is_subscribed."""
    return is_subscribed(user_id)


def activate_paid(user_id: int, months: int = 1) -> None:
    """ÃÂÃÂ»ÃÂ¸ÃÂ°ÃÂ ÃÂ´ÃÂ»ÃÂ add_subscription."""
    add_subscription(user_id, months)


def deactivate_subscription(user_id: int) -> None:
    """ÃÂÃÂµÃÂ¼ÃÂµÃÂ´ÃÂ»ÃÂµÃÂ½ÃÂ½ÃÂ¾ ÃÂ·ÃÂ°ÃÂ²ÃÂµÃÂÃÂÃÂ¸ÃÂÃÂ ÃÂ²ÃÂÃÂµ ÃÂ°ÃÂºÃÂÃÂ¸ÃÂ²ÃÂ½ÃÂÃÂµ ÃÂ¿ÃÂ¾ÃÂ´ÃÂ¿ÃÂ¸ÃÂÃÂºÃÂ¸ ÃÂ¿ÃÂ¾ÃÂ»ÃÂÃÂ·ÃÂ¾ÃÂ²ÃÂ°ÃÂÃÂµÃÂ»ÃÂ."""
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


def add_user_ticker(user_id: int, ticker: str, exchange: str = "MOEX") -> bool:
    """ÃÂÃÂ»ÃÂ¸ÃÂ°ÃÂ ÃÂ´ÃÂ»ÃÂ add_ticker."""
    return add_ticker(user_id, ticker, exchange)


def remove_user_ticker(user_id: int, ticker: str, exchange: str = "MOEX") -> None:
    """ÃÂÃÂ»ÃÂ¸ÃÂ°ÃÂ ÃÂ´ÃÂ»ÃÂ remove_ticker."""
    remove_ticker(user_id, ticker, exchange)


def get_stats() -> dict:
    """ÃÂ¡ÃÂÃÂ°ÃÂÃÂ¸ÃÂÃÂÃÂ¸ÃÂºÃÂ° ÃÂ´ÃÂ»ÃÂ /admin: ÃÂ¿ÃÂ¾ÃÂ»ÃÂÃÂ·ÃÂ¾ÃÂ²ÃÂ°ÃÂÃÂµÃÂ»ÃÂ¸, ÃÂ¿ÃÂ¾ÃÂ´ÃÂ¿ÃÂ¸ÃÂÃÂºÃÂ¸, ÃÂ°ÃÂ»ÃÂµÃÂÃÂÃÂ ÃÂ·ÃÂ° 24ÃÂ."""
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
            cur.execute(f"SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE expires_at > {PH} AND type <> 'trial'", (now_ts,))
            paid_subs = cur.fetchone()[0]
            cur.execute(
                f"SELECT COUNT(*) FROM alerts_log WHERE created_at > {PH}",
                (day_ago,)
            )
            alerts_24h = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE currency = 'XTR'")
            revenue_stars = cur.fetchone()[0]
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
            paid_subs = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE expires_at > ? AND type <> 'trial'", (now_ts,)
            ).fetchone()[0]
            revenue_stars = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE currency = 'XTR'").fetchone()[0]
    return {
        "total_users": total_users,
        "active_subs": active_subs,
        "paid_subs": paid_subs,
        "alerts_today": alerts_24h,
        "revenue_stars": int(revenue_stars or 0),
    }


# ÃÂ¦ÃÂµÃÂ½ÃÂ° ÃÂ¿ÃÂ¾ÃÂ´ÃÂ¿ÃÂ¸ÃÂÃÂºÃÂ¸ ÃÂÃÂÃÂ°ÃÂ½ÃÂ¸ÃÂÃÂÃÂ ÃÂ² ÃÂ¿ÃÂ°ÃÂ¼ÃÂÃÂÃÂ¸ (ÃÂ¼ÃÂ¾ÃÂ¶ÃÂ½ÃÂ¾ ÃÂ¿ÃÂµÃÂÃÂµÃÂ½ÃÂµÃÂÃÂÃÂ¸ ÃÂ² ÃÂÃÂ ÃÂ¿ÃÂ¾ÃÂ·ÃÂ¶ÃÂµ)
_subscription_price: dict = {"stars": 250}


def get_subscription_price() -> dict:
    """ÃÂÃÂµÃÂÃÂ½ÃÂÃÂÃÂ ÃÂÃÂµÃÂºÃÂÃÂÃÂÃÂ ÃÂÃÂµÃÂ½ÃÂ ÃÂ¿ÃÂ¾ÃÂ´ÃÂ¿ÃÂ¸ÃÂÃÂºÃÂ¸."""
    try:
        return {"stars": int(get_app_config("subscription_price_stars", str(_subscription_price["stars"])))}
    except Exception:
        return dict(_subscription_price)


def set_subscription_price(stars: int) -> None:
    """ÃÂ£ÃÂÃÂÃÂ°ÃÂ½ÃÂ¾ÃÂ²ÃÂ¸ÃÂÃÂ ÃÂÃÂµÃÂ½ÃÂ ÃÂ¿ÃÂ¾ÃÂ´ÃÂ¿ÃÂ¸ÃÂÃÂºÃÂ¸."""
    _subscription_price["stars"] = stars
    set_app_config("subscription_price_stars", str(stars))


def grant_trial(user_id: int, hours: int = TRIAL_PERIOD_HOURS) -> bool:
    """ÃÂÃÂÃÂ¸ÃÂ½ÃÂÃÂ´ÃÂ¸ÃÂÃÂµÃÂ»ÃÂÃÂ½ÃÂ¾ ÃÂ´ÃÂ°ÃÂÃÂ ÃÂÃÂÃÂ¸ÃÂ°ÃÂ» (ÃÂ´ÃÂ°ÃÂ¶ÃÂµ ÃÂµÃÂÃÂ»ÃÂ¸ ÃÂÃÂ¶ÃÂµ ÃÂ±ÃÂÃÂ»). ÃÂÃÂ»ÃÂ /admin."""
    now = datetime.now(timezone.utc)
    ensure_user(user_id)
    expires = now + timedelta(hours=max(1, hours))
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


def add_favorite(user_id: int, ticker: str, exchange: str = "MOEX") -> bool:
    """ÃÂÃÂ»ÃÂ¸ÃÂ°ÃÂ ÃÂ´ÃÂ»ÃÂ add_ticker."""
    return add_ticker(user_id, ticker, exchange)


def remove_favorite(user_id: int, ticker: str, exchange: str = "MOEX") -> None:
    """ÃÂÃÂ»ÃÂ¸ÃÂ°ÃÂ ÃÂ´ÃÂ»ÃÂ remove_ticker."""
    remove_ticker(user_id, ticker, exchange)


# ─── Shared web-admin state ───

CONFIG_DEFAULTS = {
    "subscription_price_stars": "250",
    "volume_multiplier": os.getenv("VOLUME_SPIKE_MULTIPLIER", "3.0"),
    "price_threshold_pct": os.getenv("PRICE_CHANGE_THRESHOLD_PCT", "3.0"),
    "bollinger_sigma": os.getenv("VOLATILITY_SIGMA", "2.5"),
    "min_signal_value_rub": os.getenv("MIN_SIGNAL_VALUE_RUB", "250000"),
    "morning_reports": "true",
    "evening_reports": "true",
    "alerts_enabled": "true",
}


def get_app_config(key: str, default: str | None = None) -> str:
    fallback = CONFIG_DEFAULTS.get(key, default)
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"SELECT value FROM app_config WHERE key = {PH}", (key,))
            row = cur.fetchone()
            cur.close()
        else:
            row = conn.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    if row:
        return str(row[0])
    if fallback is None:
        raise KeyError(key)
    return str(fallback)


def set_app_config(key: str, value: str) -> None:
    if key not in CONFIG_DEFAULTS:
        raise ValueError("Unknown config key")
    now = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO app_config (key, value, updated_at) VALUES ({PH}, {PH}, {PH}) "
                f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
                (key, str(value), now),
            )
            cur.close()
        else:
            conn.execute(
                "INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, str(value), now),
            )


def get_runtime_config() -> dict:
    return {
        "subscription_price_stars": int(get_app_config("subscription_price_stars")),
        "volume_multiplier": float(get_app_config("volume_multiplier")),
        "price_threshold_pct": float(get_app_config("price_threshold_pct")),
        "bollinger_sigma": float(get_app_config("bollinger_sigma")),
        "min_signal_value_rub": float(get_app_config("min_signal_value_rub")),
        "morning_reports": get_app_config("morning_reports").lower() == "true",
        "evening_reports": get_app_config("evening_reports").lower() == "true",
        "alerts_enabled": get_app_config("alerts_enabled").lower() == "true",
    }


def get_recent_payments(limit: int = 100) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT p.user_id, p.beneficiary_user_id, p.amount, p.currency, p.created_at, "
                "u.username, u.first_name FROM payments p LEFT JOIN users u ON u.user_id = p.user_id "
                "ORDER BY p.created_at DESC LIMIT %s", (limit,),
            )
            rows = cur.fetchall()
            cur.close()
        else:
            rows = conn.execute(
                "SELECT p.user_id, p.beneficiary_user_id, p.amount, p.currency, p.created_at, "
                "u.username, u.first_name FROM payments p LEFT JOIN users u ON u.user_id = p.user_id "
                "ORDER BY p.created_at DESC LIMIT ?", (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_promo_codes() -> dict[str, int]:
    with get_conn() as conn:
        query = "SELECT code, discount FROM promo_codes WHERE active = " + ("TRUE" if USE_POSTGRES else "1")
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(query)
            rows = cur.fetchall()
            cur.close()
        else:
            rows = conn.execute(query).fetchall()
    if rows:
        return {str(row[0]): int(row[1]) for row in rows}
    result = {}
    for item in os.getenv("PROMO_CODES", "WELCOME10:10").split(","):
        try:
            code, discount = item.split(":", 1)
            result[code.strip().upper()] = int(discount)
        except ValueError:
            pass
    return result


def upsert_promo_code(code: str, discount: int, active: bool = True) -> None:
    code = code.strip().upper()
    if not code or not 1 <= int(discount) <= 90:
        raise ValueError("Invalid promo code")
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO promo_codes (code, discount, active) VALUES ({PH}, {PH}, {PH}) "
                f"ON CONFLICT (code) DO UPDATE SET discount = EXCLUDED.discount, active = EXCLUDED.active",
                (code, int(discount), active),
            )
            cur.close()
        else:
            conn.execute(
                "INSERT INTO promo_codes (code, discount, active) VALUES (?, ?, ?) "
                "ON CONFLICT (code) DO UPDATE SET discount = excluded.discount, active = excluded.active",
                (code, int(discount), int(active)),
            )


def disable_promo_code(code: str) -> None:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"UPDATE promo_codes SET active = FALSE WHERE code = {PH}", (code.upper(),))
            cur.close()
        else:
            conn.execute("UPDATE promo_codes SET active = 0 WHERE code = ?", (code.upper(),))


def create_admin_login_code(user_id: int, ttl_seconds: int = 300) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    digest = hashlib.sha256(code.encode()).hexdigest()
    now = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM admin_login_codes WHERE user_id = {PH} OR expires_at < {PH}", (user_id, now))
            cur.execute(f"INSERT INTO admin_login_codes (code_hash, user_id, expires_at) VALUES ({PH}, {PH}, {PH})", (digest, user_id, now + ttl_seconds))
            cur.close()
        else:
            conn.execute("DELETE FROM admin_login_codes WHERE user_id = ? OR expires_at < ?", (user_id, now))
            conn.execute("INSERT INTO admin_login_codes (code_hash, user_id, expires_at) VALUES (?, ?, ?)", (digest, user_id, now + ttl_seconds))
    return code


def exchange_admin_login_code(code: str, allowed_admin_ids: list[int], ttl_hours: int = 12) -> tuple[str, int] | None:
    digest = hashlib.sha256(code.strip().encode()).hexdigest()
    now = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"SELECT user_id FROM admin_login_codes WHERE code_hash = {PH} AND expires_at > {PH} AND used_at IS NULL", (digest, now))
            row = cur.fetchone()
            if not row or int(row[0]) not in allowed_admin_ids:
                cur.close()
                return None
            user_id = int(row[0])
            cur.execute(f"UPDATE admin_login_codes SET used_at = {PH} WHERE code_hash = {PH}", (now, digest))
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            cur.execute(f"INSERT INTO admin_sessions (token_hash, user_id, expires_at) VALUES ({PH}, {PH}, {PH})", (token_hash, user_id, now + ttl_hours * 3600))
            cur.close()
        else:
            row = conn.execute("SELECT user_id FROM admin_login_codes WHERE code_hash = ? AND expires_at > ? AND used_at IS NULL", (digest, now)).fetchone()
            if not row or int(row[0]) not in allowed_admin_ids:
                return None
            user_id = int(row[0])
            conn.execute("UPDATE admin_login_codes SET used_at = ? WHERE code_hash = ?", (now, digest))
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            conn.execute("INSERT INTO admin_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)", (token_hash, user_id, now + ttl_hours * 3600))
    return token, user_id


def verify_admin_session(token: str) -> int | None:
    if not token:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"SELECT user_id FROM admin_sessions WHERE token_hash = {PH} AND expires_at > {PH}", (digest, now))
            row = cur.fetchone()
            cur.close()
        else:
            row = conn.execute("SELECT user_id FROM admin_sessions WHERE token_hash = ? AND expires_at > ?", (digest, now)).fetchone()
    return int(row[0]) if row else None


def _hash_admin_password(password: str, salt: str | None = None) -> str:
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240_000).hex()
    return f"pbkdf2_sha256$240000${salt}${digest}"


def _verify_admin_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, expected = stored.split("$", 3)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False


def ensure_admin_owner(username: str, password: str) -> None:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"SELECT id FROM admin_staff WHERE username = {PH}", (username,))
            exists = cur.fetchone()
            if not exists:
                cur.execute(f"INSERT INTO admin_staff (username, password_hash, role) VALUES ({PH}, {PH}, 'owner')", (username, _hash_admin_password(password)))
            cur.close()
        else:
            exists = conn.execute("SELECT id FROM admin_staff WHERE username = ?", (username,)).fetchone()
            if not exists:
                conn.execute("INSERT INTO admin_staff (username, password_hash, role) VALUES (?, ?, 'owner')", (username, _hash_admin_password(password)))


def authenticate_admin_staff(username: str, password: str) -> dict | None:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT id, username, password_hash, role, active FROM admin_staff WHERE username = {PH}", (username,))
            row = cur.fetchone()
            cur.close()
        else:
            row = conn.execute("SELECT id, username, password_hash, role, active FROM admin_staff WHERE username = ?", (username,)).fetchone()
    if not row or not bool(row["active"] if hasattr(row, "keys") else row[4]):
        return None
    data = dict(row) if hasattr(row, "keys") else {"id": row[0], "username": row[1], "password_hash": row[2], "role": row[3], "active": row[4]}
    return data if _verify_admin_password(password, data["password_hash"]) else None


def create_admin_session(user_id: int, ttl_hours: int = 12, username: str = "admin", role: str = "owner") -> str:
    """Create a web session after credentials have been verified."""
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO admin_sessions (token_hash, user_id, expires_at, username, role) VALUES ({PH}, {PH}, {PH}, {PH}, {PH})",
                (digest, user_id, now + ttl_hours * 3600, username, role),
            )
            cur.close()
        else:
            conn.execute(
                "INSERT INTO admin_sessions (token_hash, user_id, expires_at, username, role) VALUES (?, ?, ?, ?, ?)",
                (digest, user_id, now + ttl_hours * 3600, username, role),
            )
    return token


def verify_admin_session_info(token: str) -> dict | None:
    if not token:
        return None
    digest, now = hashlib.sha256(token.encode()).hexdigest(), datetime.now(timezone.utc).timestamp()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"SELECT user_id, username, role FROM admin_sessions WHERE token_hash = {PH} AND expires_at > {PH}", (digest, now))
            row = cur.fetchone(); cur.close()
        else:
            row = conn.execute("SELECT user_id, username, role FROM admin_sessions WHERE token_hash = ? AND expires_at > ?", (digest, now)).fetchone()
    return dict(row) if row else None


def list_admin_staff() -> list[dict]:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT id, username, role, active, created_at FROM admin_staff ORDER BY id"); rows = cur.fetchall(); cur.close()
        else:
            rows = conn.execute("SELECT id, username, role, active, created_at FROM admin_staff ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def create_admin_staff(username: str, password: str, role: str) -> None:
    username = username.strip().lower()
    if not username or role not in {"admin", "support", "analyst"}:
        raise ValueError("Invalid staff account")
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(); cur.execute(f"INSERT INTO admin_staff (username, password_hash, role) VALUES ({PH}, {PH}, {PH})", (username, _hash_admin_password(password), role)); cur.close()
        else:
            conn.execute("INSERT INTO admin_staff (username, password_hash, role) VALUES (?, ?, ?)", (username, _hash_admin_password(password), role))


def update_admin_staff(staff_id: int, *, password: str | None = None, role: str | None = None, active: bool | None = None) -> None:
    updates, params = [], []
    if password is not None: updates.append(f"password_hash = {PH}"); params.append(_hash_admin_password(password))
    if role is not None:
        if role not in {"admin", "support", "analyst"}: raise ValueError("Invalid role")
        updates.append(f"role = {PH}"); params.append(role)
    if active is not None: updates.append(f"active = {PH}"); params.append(bool(active) if USE_POSTGRES else int(active))
    if not updates: return
    params.append(staff_id)
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(); cur.execute(f"UPDATE admin_staff SET {', '.join(updates)} WHERE id = {PH} AND role <> 'owner'", tuple(params)); cur.close()
        else:
            conn.execute(f"UPDATE admin_staff SET {', '.join(updates)} WHERE id = ? AND role <> 'owner'", tuple(params))


def change_admin_password(username: str, current_password: str, new_password: str) -> bool:
    staff = authenticate_admin_staff(username, current_password)
    if not staff:
        return False
    password_hash = _hash_admin_password(new_password)
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(); cur.execute(f"UPDATE admin_staff SET password_hash = {PH} WHERE username = {PH}", (password_hash, username)); cur.close()
        else:
            conn.execute("UPDATE admin_staff SET password_hash = ? WHERE username = ?", (password_hash, username))
    return True


def revoke_admin_session(token: str) -> None:
    digest = hashlib.sha256(token.encode()).hexdigest()
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM admin_sessions WHERE token_hash = {PH}", (digest,))
            cur.close()
        else:
            conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (digest,))


def log_admin_action(admin_id: int, action: str, details: str = "") -> None:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"INSERT INTO admin_audit (admin_id, action, details) VALUES ({PH}, {PH}, {PH})", (admin_id, action, details[:1000]))
            cur.close()
        else:
            conn.execute("INSERT INTO admin_audit (admin_id, action, details) VALUES (?, ?, ?)", (admin_id, action, details[:1000]))


def get_admin_audit(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT admin_id, action, details, created_at FROM admin_audit ORDER BY created_at DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
            cur.close()
        else:
            rows = conn.execute("SELECT admin_id, action, details, created_at FROM admin_audit ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]
