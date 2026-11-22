"""
Конфигурация бота.
Все секреты — через переменные окружения (or .env файл).
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Telegram ───────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_IDS: list[int] = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]

# ─── Подписка ───────────────────────────────────────────────────────────────
# Цена в Telegram Stars (1 Star ≈ 0.013 USD)
SUBSCRIPTION_PRICE_STARS: int = int(os.getenv("SUBSCRIPTION_PRICE_STARS", "250"))
SUBSCRIPTION_PERIOD_DAYS: int = 30          # длительность подписки
TRIAL_PERIOD_HOURS: int = 24                # пробный период

SUBSCRIPTION_TITLE = "📈 AnomalyAlert — Подписка на 30 дней"
SUBSCRIPTION_DESCRIPTION = (
    "Мгновенные алерты об аномальных событиях на Московской бирже и СПБ Бирже: "
    "спайки объёма, аномальный рост/падение, необычная волатильность и многое другое."
)

# ─── Рыночные данные ───────────────────────────────────────────────────────────
# MOEX ISS — открытый REST-API, не требует ключа
MOEX_ISS_BASE = "https://iss.moex.com/iss"

# Интервал опроса биржи (секунды)
POLL_INTERVAL_SEC: int = int(os.getenv("POLL_INTERVAL_SEC", "60"))
SCAN_INTERVAL: int = POLL_INTERVAL_SEC  # алиас для main.py

# ─── Пороги аномалий ─────────────────────────────────────────────────────────────
# Объём выше среднего в N раз считается аномальным
VOLUME_SPIKE_MULTIPLIER: float = float(os.getenv("VOLUME_SPIKE_MULTIPLIER", "3.0"))
# Изменение цены (%) за один интервал
PRICE_CHANGE_THRESHOLD_PCT: float = float(os.getenv("PRICE_CHANGE_THRESHOLD_PCT", "3.0"))
# Ширина BB-полосы (σ) для сигнала волатильности
VOLATILITY_SIGMA: float = float(os.getenv("VOLATILITY_SIGMA", "2.5"))

# Количество баров истории для расчёта скользящих средних
HISTORY_BARS: int = 20

# ─── Поддержка ───────────────────────────────────────────────────────────────
# Telegram username администратора (без @) для кнопки "Поддержка"
SUPPORT_USERNAME: str = os.getenv("SUPPORT_USERNAME", "")

# ─── База данных ───────────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "data/bot.db")

# ─── Логирование ─────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE: str = os.getenv("LOG_FILE", "logs/bot.log")
