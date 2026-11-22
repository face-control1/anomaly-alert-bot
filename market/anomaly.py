"""
Детектор аномалий на рыночных данных.

Три типа сигналов:
  1. volume_spike   - объём в N раз выше среднего
  2. price_move     - изменение цены > X% за интервал
  3. volatility     - цена пробила полосу Боллинджера
"""
import statistics
import logging
from dataclasses import dataclass
from config import (
    VOLUME_SPIKE_MULTIPLIER,
    PRICE_CHANGE_THRESHOLD_PCT,
    VOLATILITY_SIGMA,
    HISTORY_BARS,
)

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    ticker: str
    exchange: str
    alert_type: str
    emoji: str
    headline: str
    details: str


def _mean_std(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        return (values[0] if values else 0.0), 0.0
    mu = statistics.mean(values)
    sigma = statistics.stdev(values)
    return mu, sigma


def detect_volume_spike(
    ticker: str,
    exchange: str,
    current_volume: float,
    history_volumes: list[float],
) -> Alert | None:
    if not history_volumes or current_volume <= 0:
        return None
    mu, _ = _mean_std(history_volumes)
    if mu <= 0:
        return None
    ratio = current_volume / mu
    if ratio >= VOLUME_SPIKE_MULTIPLIER:
        return Alert(
            ticker=ticker,
            exchange=exchange,
            alert_type="volume_spike",
            emoji="🔊",
            headline=f"Аномальный объём: {ticker} ({exchange})",
            details=(
                f"Текущий объём {current_volume:,.0f} — "
                f"в {ratio:.1f}x выше среднего ({mu:,.0f})"
            ),
        )
    return None


def detect_price_move(
    ticker: str,
    exchange: str,
    prev_price: float,
    curr_price: float,
) -> Alert | None:
    if prev_price <= 0 or curr_price <= 0:
        return None
    change_pct = (curr_price - prev_price) / prev_price * 100
    if abs(change_pct) >= PRICE_CHANGE_THRESHOLD_PCT:
        direction = "🚀 Рост" if change_pct > 0 else "🔻 Падение"
        return Alert(
            ticker=ticker,
            exchange=exchange,
            alert_type="price_move",
            emoji="🚨",
            headline=f"Резкое движение цены: {ticker} ({exchange})",
            details=(
                f"{direction} на {abs(change_pct):.2f}% | "
                f"{prev_price:.2f} -> {curr_price:.2f}"
            ),
        )
    return None


def detect_bollinger_break(
    ticker: str,
    exchange: str,
    history_closes: list[float],
    current_price: float,
) -> Alert | None:
    if len(history_closes) < HISTORY_BARS or current_price <= 0:
        return None
    mu, sigma = _mean_std(history_closes)
    if sigma <= 0:
        return None
    upper = mu + VOLATILITY_SIGMA * sigma
    lower = mu - VOLATILITY_SIGMA * sigma
    if current_price > upper:
        return Alert(
            ticker=ticker,
            exchange=exchange,
            alert_type="volatility",
            emoji="📈",
            headline=f"Пробой верхней полосы Боллинджера: {ticker} ({exchange})",
            details=(
                f"Цена {current_price:.2f} выше верхней полосы {upper:.2f} "
                f"(mu={mu:.2f}, sigma={sigma:.2f})"
            ),
        )
    if current_price < lower:
        return Alert(
            ticker=ticker,
            exchange=exchange,
            alert_type="volatility",
            emoji="📉",
            headline=f"Пробой нижней полосы Боллинджера: {ticker} ({exchange})",
            details=(
                f"Цена {current_price:.2f} ниже нижней полосы {lower:.2f} "
                f"(mu={mu:.2f}, sigma={sigma:.2f})"
            ),
        )
    return None


def run_all_detectors(
    ticker: str,
    exchange: str,
    candles: list[dict],
) -> list[Alert]:
    """Запускает все детекторы по набору свечей. Возвращает список найденных алертов."""
    if len(candles) < 2:
        return []

    closes = []
    volumes = []
    for c in candles:
        cl = c.get("close") or c.get("CLOSE")
        vol = c.get("volume") or c.get("VOLUME") or 0
        if cl:
            closes.append(float(cl))
            volumes.append(float(vol))

    if len(closes) < 2:
        return []

    alerts: list[Alert] = []
    current_price = closes[-1]
    prev_price = closes[-2]
    current_vol = volumes[-1]
    history_vols = volumes[:-1]
    history_cls = closes[:-1]

    a = detect_price_move(ticker, exchange, prev_price, current_price)
    if a:
        alerts.append(a)

    a = detect_volume_spike(ticker, exchange, current_vol, history_vols)
    if a:
        alerts.append(a)

    if len(history_cls) >= HISTORY_BARS:
        a = detect_bollinger_break(ticker, exchange, history_cls[-HISTORY_BARS:],
                                    current_price)
        if a:
            alerts.append(a)

    return alerts
