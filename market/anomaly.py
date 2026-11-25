"""
Детекторы аномалий для рыночных данных.

Архитектура пайплайна:
  Ingestion → Pre-Filter (freshness) → Detectors → Post-Filter → Dispatch

Детекторы используют робастную статистику (медиана + IQR) вместо EMA/mean,
чтобы не «догонять» аномалию базовой линией.
"""
from __future__ import annotations

import statistics
import time
import html
from config import MIN_SIGNAL_VALUE_RUB
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional


MSK = timezone(timedelta(hours=3))


# ─── Настройки пост-фильтра ──────────────────────────────────────────────────

# Кулдаун: после отправки алерта по инструменту блокируем повторную отправку N сек
ALERT_COOLDOWN_SECONDS = 60

# Минимальный размер объёма (в лотах), ниже которого сигнал игнорируем
MIN_VOLUME_LOTS = 1

# Порог «свежести» данных в миллисекундах (5 секунд)
STALE_AFTER_MS = 5_000


@dataclass
class Alert:
    ticker: str
    short_name: str          # краткое название (напр. "Сбербанк")
    exchange: str            # "MOEX"
    alert_type: str          # "volume_spike" | "price_move" | "bollinger"
    emoji: str
    headline: str            # заголовок алерта
    details: str             # детали

    # Рыночные данные
    current_price: float = 0.0        # последняя цена
    volume_1m: int = 0                # объём за текущую минуту (лоты)
    value_1m: float = 0.0             # объём в рублях за минуту
    price_change_1m_pct: float = 0.0  # изменение за минуту %
    price_change_day_pct: float = 0.0 # изменение за день %
    price_change_week_pct: Optional[float] = None  # изменение за неделю %
    score: int = 50
    volume_ratio: float = 0.0
    trigger_reason: str = ""
    alert_time: str = field(default_factory=lambda: datetime.now(MSK).strftime("%H:%M:%S"))

    # Метка времени получения события (мс) — для Freshness-фильтра
    received_at_ms: float = field(default_factory=lambda: time.monotonic() * 1000)

    def is_fresh(self, stale_after_ms: float = STALE_AFTER_MS) -> bool:
        """Проверяет, не устарело ли событие."""
        age = time.monotonic() * 1000 - self.received_at_ms
        return age <= stale_after_ms

    def format_message(self) -> str:
        """Форматирует сообщение для отправки в Telegram."""
        week_str = (
            f"{'+' if (self.price_change_week_pct or 0) >= 0 else ''}{self.price_change_week_pct:.2f}%"
            if self.price_change_week_pct is not None
            else "н/д"
        )
        day_sign = "+" if self.price_change_day_pct >= 0 else ""
        min_sign = "+" if self.price_change_1m_pct >= 0 else ""

        ticker = html.escape(self.ticker)
        short_name = html.escape(self.short_name)
        headline = html.escape(self.headline)
        details = html.escape(self.details)
        reason = html.escape(self.trigger_reason or self.headline)
        if self.score >= 80:
            score_badge = "🔴 сильный"
        elif self.score >= 60:
            score_badge = "🟡 заметный"
        else:
            score_badge = "🟢 умеренный"
        return (
            f"{self.emoji} <b>{ticker}</b>  ·  {short_name}\n"
            f"<code>{self.exchange}  |  {self.alert_time} МСК</code>\n"
            f"━━━━━━━━━━━━━━\n"
            f"<b>{headline}</b>\n"
            f"{details}\n\n"
            f"🎯 Рейтинг: <b>{self.score}/100</b> · {score_badge}\n"
            f"💡 Почему: {reason}\n"
            f"💰 <b>{self.current_price:,.2f} ₽</b>\n"
            f"📊 {self.volume_1m:,} лот"
            + (f" / {self.value_1m/1_000_000:.2f}M ₽" if self.value_1m > 0 else "")
            + f"\n"
            f"⏱ 1 мин: <b>{min_sign}{self.price_change_1m_pct:.2f}%</b>  ·  "
            f"день: <b>{day_sign}{self.price_change_day_pct:.2f}%</b>\n"
            f"📆 Неделя: <b>{week_str}</b>"
        )


# ─── Вспомогательные функции ─────────────────────────────────────────────────

def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _is_opening_auction_noise(candle: dict) -> bool:
    """The first ten minutes have structurally incomparable auction volume."""
    raw = candle.get("begin") or candle.get("time")
    if not raw:
        return False
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return value.hour == 10 and value.minute < 10
    except (TypeError, ValueError):
        return False


def _robust_z(values: list[float]) -> tuple[float, float, float]:
    """
    Возвращает (медиана, IQR, нормировочный коэффициент σ̂).
    σ̂ = IQR * 1.4826  — несмещённая оценка σ при нормальном распределении.
    Устойчива к выбросам в отличие от mean/std.
    """
    if len(values) < 4:
        med = statistics.median(values) if values else 0.0
        return med, 0.0, 0.0
    q1 = statistics.quantiles(values, n=4)[0]  # 25-й перцентиль
    q3 = statistics.quantiles(values, n=4)[2]  # 75-й перцентиль
    iqr = q3 - q1
    sigma_hat = iqr * 1.4826
    return statistics.median(values), iqr, sigma_hat


# ─── Кулдаун (пост-фильтр) ───────────────────────────────────────────────────

class AlertCooldownFilter:
    """
    Пост-фильтр: блокирует повторные алерты по (ticker, alert_type)
    в течение cooldown_seconds секунд.
    Шаг 5 по архитектуре: Anti-False-Positive / Cooldown.
    """
    def __init__(self, cooldown_seconds: float = ALERT_COOLDOWN_SECONDS):
        self._cooldown = cooldown_seconds
        self._last_sent: dict[tuple, float] = {}

    def allow(self, alert: Alert) -> bool:
        """Возвращает True, если алерт разрешён к отправке."""
        key = (alert.ticker, alert.alert_type)
        now = time.monotonic()
        last = self._last_sent.get(key, 0.0)
        if now - last < self._cooldown:
            return False
        self._last_sent[key] = now
        return True

    def merge_cross_alerts(self, alerts: list[Alert]) -> list[Alert]:
        """
        Кросс-проверка (Шаг 5): если volume_spike и bollinger/price_move
        срабатывают одновременно на одном инструменте — объединяем в один
        комплексный алерт с повышенным приоритетом.
        """
        by_ticker: dict[str, list[Alert]] = {}
        for a in alerts:
            by_ticker.setdefault(a.ticker, []).append(a)

        result: list[Alert] = []
        for ticker, ticker_alerts in by_ticker.items():
            types = {a.alert_type for a in ticker_alerts}
            if "volume_spike" in types and len(types) > 1:
                # Берём volume_spike как основной, обогащаем деталями
                primary = next(a for a in ticker_alerts if a.alert_type == "volume_spike")
                secondary_headlines = [
                    a.headline for a in ticker_alerts if a.alert_type != "volume_spike"
                ]
                combined = Alert(
                    ticker=primary.ticker,
                    short_name=primary.short_name,
                    exchange=primary.exchange,
                    alert_type="combined",
                    emoji="🚨",
                    headline=f"КОМПЛЕКСНЫЙ СИГНАЛ: {primary.headline}",
                    details=primary.details + "\n⚠️ " + " | ".join(secondary_headlines),
                    current_price=primary.current_price,
                    volume_1m=primary.volume_1m,
                    value_1m=primary.value_1m,
                    price_change_1m_pct=primary.price_change_1m_pct,
                    price_change_day_pct=primary.price_change_day_pct,
                    price_change_week_pct=primary.price_change_week_pct,
                    score=min(100, max(a.score for a in ticker_alerts) + 12),
                    volume_ratio=primary.volume_ratio,
                    trigger_reason="одновременно сработали несколько независимых индикаторов",
                    alert_time=primary.alert_time,
                    received_at_ms=primary.received_at_ms,
                )
                result.append(combined)
            else:
                result.extend(ticker_alerts)
        return result


# ─── Детекторы ───────────────────────────────────────────────────────────────

def detect_volume_spike(
    ticker: str,
    short_name: str,
    candles: list[dict],
    marketdata: dict,
    securities: dict,
    multiplier: float = 3.0,
    weekly_change: Optional[float] = None,
) -> Optional[Alert]:
    """
    Аномальный объём: робастный Z-score (медиана + IQR) последней свечи.
    Шаг 2 по архитектуре: Детектор аномалий объёма и цены.

    Требует подтверждения контекстом: объём И цена должны быть аномальными.
    """
    if len(candles) < 5:
        return None

    volumes = [_safe_float(c.get("volume")) for c in candles[:-1] if _safe_float(c.get("volume")) > 0]
    if len(volumes) < 4:
        return None

    last_vol = _safe_float(candles[-1].get("volume"))
    last_value = _safe_float(candles[-1].get("value"))
    if last_vol < MIN_VOLUME_LOTS:
        return None
    if last_value < MIN_SIGNAL_VALUE_RUB:
        return None

    median_vol, iqr_vol, sigma_vol = _robust_z(volumes)
    if sigma_vol == 0:
        # Fallback: простое сравнение с медианой
        if median_vol == 0 or last_vol < median_vol * multiplier:
            return None
        ratio = last_vol / median_vol
    else:
        z_vol = (last_vol - median_vol) / sigma_vol
        if z_vol < multiplier:
            return None
        ratio = last_vol / median_vol if median_vol > 0 else 0

    current_price = _safe_float(marketdata.get("LAST"))
    prev_price = _safe_float(securities.get("PREVPRICE"))
    open_price = _safe_float(candles[-1].get("open"))
    close_price = _safe_float(candles[-1].get("close"))

    price_change_1m = round((close_price - open_price) / open_price * 100, 2) if open_price else 0.0
    price_change_day = round((current_price - prev_price) / prev_price * 100, 2) if prev_price else 0.0

    # Подтверждение контекстом: движение цены тоже аномальное
    prices = [_safe_float(c.get("close")) for c in candles[:-1] if _safe_float(c.get("close")) > 0]
    if len(prices) >= 4:
        _, _, sigma_price = _robust_z(prices)
        if sigma_price > 0:
            z_price = abs(close_price - statistics.median(prices)) / sigma_price
            # Если цена не движется — скорее всего технический набор позиции
            if z_price < 1.0 and abs(price_change_1m) < 0.5:
                return None

    return Alert(
        ticker=ticker,
        short_name=short_name,
        exchange="MOEX",
        alert_type="volume_spike",
        emoji="🔥",
        headline=f"Всплеск объёма × {ratio:.1f} от нормы",
        details=f"Текущий объём: {int(last_vol):,} лот | Медиана: {int(median_vol):,} лот",
        current_price=current_price,
        volume_1m=int(last_vol),
        value_1m=last_value,
        price_change_1m_pct=price_change_1m,
        price_change_day_pct=price_change_day,
        price_change_week_pct=weekly_change,
        score=min(100, max(40, round(42 + min(ratio, 10) * 5 + min(abs(price_change_1m), 5) * 3))),
        volume_ratio=round(ratio, 2),
        trigger_reason=f"объём в {ratio:.1f}× выше медианы при обороте {last_value / 1_000_000:.2f} млн ₽",
    )


def detect_price_move(
    ticker: str,
    short_name: str,
    candles: list[dict],
    marketdata: dict,
    securities: dict,
    threshold_pct: float = 3.0,
    weekly_change: Optional[float] = None,
) -> Optional[Alert]:
    """
    Резкое движение цены за одну минуту.
    Использует robust Z-score для динамического порога.
    """
    if len(candles) < 5:
        return None

    last = candles[-1]
    open_price = _safe_float(last.get("open"))
    close_price = _safe_float(last.get("close"))
    if open_price == 0:
        return None

    pct = (close_price - open_price) / open_price * 100

    # Динамический порог на основе IQR исторических движений
    price_changes = []
    for c in candles[:-1]:
        o = _safe_float(c.get("open"))
        cl = _safe_float(c.get("close"))
        if o > 0:
            price_changes.append(abs((cl - o) / o * 100))

    if len(price_changes) >= 4:
        _, _, sigma_pct = _robust_z(price_changes)
        # Динамический порог: не ниже threshold_pct, но адаптируется к волатильности
        dynamic_threshold = max(threshold_pct, statistics.median(price_changes) + 2 * sigma_pct)
    else:
        dynamic_threshold = threshold_pct

    if abs(pct) < dynamic_threshold:
        return None

    current_price = _safe_float(marketdata.get("LAST")) or close_price
    prev_price = _safe_float(securities.get("PREVPRICE"))
    price_change_day = round((current_price - prev_price) / prev_price * 100, 2) if prev_price else 0.0
    volume = _safe_float(last.get("volume"))
    value = _safe_float(last.get("value"))

    if volume < MIN_VOLUME_LOTS or value < MIN_SIGNAL_VALUE_RUB:
        return None

    direction = "📈 Рост" if pct > 0 else "📉 Падение"
    emoji = "📈" if pct > 0 else "📉"

    return Alert(
        ticker=ticker,
        short_name=short_name,
        exchange="MOEX",
        alert_type="price_move",
        emoji=emoji,
        headline=f"{direction} на {abs(pct):.2f}% за минуту (порог {dynamic_threshold:.1f}%)",
        details=f"Открытие: {open_price:.2f} ₽ → Закрытие: {close_price:.2f} ₽",
        current_price=current_price,
        volume_1m=int(volume),
        value_1m=value,
        price_change_1m_pct=round(pct, 2),
        price_change_day_pct=price_change_day,
        price_change_week_pct=weekly_change,
        score=min(100, max(45, round(50 + abs(pct) * 8 + min(value / 1_000_000, 10)))),
        trigger_reason=f"движение {pct:+.2f}% превысило динамический порог {dynamic_threshold:.2f}%",
    )


def detect_bollinger_break(
    ticker: str,
    short_name: str,
    candles: list[dict],
    marketdata: dict,
    securities: dict,
    sigma: float = 2.5,
    weekly_change: Optional[float] = None,
) -> Optional[Alert]:
    """
    Выход цены за полосы Боллинджера.
    Использует робастные полосы на базе медианы + IQR.
    """
    if len(candles) < 10:
        return None

    closes = [_safe_float(c.get("close")) for c in candles[:-1] if _safe_float(c.get("close")) > 0]
    if len(closes) < 10:
        return None

    median_price, iqr, sigma_hat = _robust_z(closes)
    if sigma_hat == 0:
        # Fallback: классические полосы Боллинджера
        mean = statistics.mean(closes)
        std = statistics.stdev(closes)
        if std == 0:
            return None
        upper = mean + sigma * std
        lower = mean - sigma * std
        band_ref = mean
    else:
        upper = median_price + sigma * sigma_hat
        lower = median_price - sigma * sigma_hat
        band_ref = median_price

    last = candles[-1]
    close_price = _safe_float(last.get("close"))
    open_price = _safe_float(last.get("open"))
    volume = _safe_float(last.get("volume"))
    value = _safe_float(last.get("value"))

    if lower < close_price < upper:
        return None

    if volume < MIN_VOLUME_LOTS or value < MIN_SIGNAL_VALUE_RUB:
        return None

    current_price = _safe_float(marketdata.get("LAST")) or close_price
    prev_price = _safe_float(securities.get("PREVPRICE"))
    price_change_day = round((current_price - prev_price) / prev_price * 100, 2) if prev_price else 0.0
    price_change_1m = round((close_price - open_price) / open_price * 100, 2) if open_price else 0.0

    if close_price > upper:
        emoji, direction, band_val = "⚡️", "выше верхней полосы BB", upper
    else:
        emoji, direction, band_val = "⚡️", "ниже нижней полосы BB", lower

    return Alert(
        ticker=ticker,
        short_name=short_name,
        exchange="MOEX",
        alert_type="bollinger",
        emoji=emoji,
        headline=f"Выход {direction} ({sigma}σ)",
        details=f"Цена: {close_price:.2f} ₽ | Полоса: {band_val:.2f} ₽ | Медиана: {band_ref:.2f} ₽",
        current_price=current_price,
        volume_1m=int(volume),
        value_1m=value,
        price_change_1m_pct=price_change_1m,
        price_change_day_pct=price_change_day,
        price_change_week_pct=weekly_change,
        score=min(100, max(45, round(55 + abs(close_price - band_val) / max(band_ref, 0.01) * 1000))),
        trigger_reason=f"цена вышла {direction} при обороте {value / 1_000_000:.2f} млн ₽",
    )


def run_all_detectors(
    ticker: str,
    short_name: str,
    candles: list[dict],
    marketdata: dict,
    securities: dict,
    weekly_change: Optional[float] = None,
    volume_multiplier: float = 3.0,
    price_threshold_pct: float = 3.0,
    bollinger_sigma: float = 2.5,
) -> list[Alert]:
    if not candles or _is_opening_auction_noise(candles[-1]):
        return []
    alerts: list[Alert] = []
    for fn in (detect_volume_spike, detect_price_move, detect_bollinger_break):
        try:
            kwargs = dict(
                ticker=ticker,
                short_name=short_name,
                candles=candles,
                marketdata=marketdata,
                securities=securities,
                weekly_change=weekly_change,
            )
            if fn == detect_volume_spike:
                kwargs["multiplier"] = volume_multiplier
            elif fn == detect_price_move:
                kwargs["threshold_pct"] = price_threshold_pct
            elif fn == detect_bollinger_break:
                kwargs["sigma"] = bollinger_sigma
            result = fn(**kwargs)
            if result:
                alerts.append(result)
        except Exception:
            pass
    return alerts
