"""
Детекторы аномалий для рыночных данных.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Alert:
    ticker: str
    short_name: str
    exchange: str
    alert_type: str
    emoji: str
    headline: str
    details: str

    current_price: float = 0.0
    volume_1m: int = 0
    value_1m: float = 0.0
    price_change_1m_pct: float = 0.0
    price_change_day_pct: float = 0.0
    price_change_week_pct: Optional[float] = None
    alert_time: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))

    def format_message(self) -> str:
        week_str = (
            f"{'+' if (self.price_change_week_pct or 0) >= 0 else ''}{self.price_change_week_pct:.2f}%"
            if self.price_change_week_pct is not None
            else "н/д"
        )
        day_sign = "+" if self.price_change_day_pct >= 0 else ""
        min_sign = "+" if self.price_change_1m_pct >= 0 else ""
        return (
            f"{self.emoji} <b>{self.ticker}</b> — {self.short_name}\n"
            f"🕐 {self.alert_time} | {self.exchange}\n\n"
            f"⚡️ <b>{self.headline}</b>\n"
            f"{self.details}\n\n"
            f"💰 Цена: <b>{self.current_price:,.2f} ₽</b>\n"
            f"📊 Объём (мин): <b>{self.volume_1m:,} лот</b>"
            + (f" / {self.value_1m/1_000_000:.2f}M ₽" if self.value_1m > 0 else "")
            + f"\n⏱ За минуту: <b>{min_sign}{self.price_change_1m_pct:.2f}%</b>\n"
            f"📅 За день:   <b>{day_sign}{self.price_change_day_pct:.2f}%</b>\n"
            f"📆 За неделю: <b>{week_str}</b>"
        )


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def detect_volume_spike(ticker, short_name, candles, marketdata, securities, multiplier=3.0, weekly_change=None):
    if len(candles) < 5:
        return None
    volumes = [_safe_float(c.get("volume")) for c in candles[:-1] if _safe_float(c.get("volume")) > 0]
    if len(volumes) < 4:
        return None
    avg_vol = statistics.mean(volumes)
    if avg_vol == 0:
        return None
    last_vol = _safe_float(candles[-1].get("volume"))
    if last_vol < avg_vol * multiplier:
        return None
    current_price = _safe_float(marketdata.get("LAST"))
    prev_price = _safe_float(securities.get("PREVPRICE"))
    open_price = _safe_float(candles[-1].get("open"))
    close_price = _safe_float(candles[-1].get("close"))
    price_change_1m = round((close_price - open_price) / open_price * 100, 2) if open_price else 0.0
    price_change_day = round((current_price - prev_price) / prev_price * 100, 2) if prev_price else 0.0
    return Alert(ticker=ticker, short_name=short_name, exchange="MOEX", alert_type="volume_spike",
        emoji="🔥", headline=f"Всплеск объёма × {last_vol/avg_vol:.1f} от нормы",
        details=f"Текущий: {int(last_vol):,} лот | Средний: {int(avg_vol):,} лот",
        current_price=current_price, volume_1m=int(last_vol), value_1m=last_vol*current_price,
        price_change_1m_pct=price_change_1m, price_change_day_pct=price_change_day, price_change_week_pct=weekly_change)


def detect_price_move(ticker, short_name, candles, marketdata, securities, threshold_pct=3.0, weekly_change=None):
    if len(candles) < 2:
        return None
    last = candles[-1]
    open_price = _safe_float(last.get("open"))
    close_price = _safe_float(last.get("close"))
    if open_price == 0:
        return None
    pct = (close_price - open_price) / open_price * 100
    if abs(pct) < threshold_pct:
        return None
    current_price = _safe_float(marketdata.get("LAST")) or close_price
    prev_price = _safe_float(securities.get("PREVPRICE"))
    price_change_day = round((current_price - prev_price) / prev_price * 100, 2) if prev_price else 0.0
    volume = _safe_float(last.get("volume"))
    emoji = "📈" if pct > 0 else "📉"
    direction = "📈 Рост" if pct > 0 else "📉 Падение"
    return Alert(ticker=ticker, short_name=short_name, exchange="MOEX", alert_type="price_move",
        emoji=emoji, headline=f"{direction} на {abs(pct):.2f}% за минуту",
        details=f"Открытие: {open_price:.2f} ₽ → {close_price:.2f} ₽",
        current_price=current_price, volume_1m=int(volume), value_1m=volume*current_price,
        price_change_1m_pct=round(pct, 2), price_change_day_pct=price_change_day, price_change_week_pct=weekly_change)


def detect_bollinger_break(ticker, short_name, candles, marketdata, securities, sigma=2.5, weekly_change=None):
    if len(candles) < 10:
        return None
    closes = [_safe_float(c.get("close")) for c in candles[:-1] if _safe_float(c.get("close")) > 0]
    if len(closes) < 10:
        return None
    mean = statistics.mean(closes)
    std = statistics.stdev(closes)
    if std == 0:
        return None
    upper = mean + sigma * std
    lower = mean - sigma * std
    last = candles[-1]
    close_price = _safe_float(last.get("close"))
    open_price = _safe_float(last.get("open"))
    volume = _safe_float(last.get("volume"))
    if lower < close_price < upper:
        return None
    current_price = _safe_float(marketdata.get("LAST")) or close_price
    prev_price = _safe_float(securities.get("PREVPRICE"))
    price_change_day = round((current_price - prev_price) / prev_price * 100, 2) if prev_price else 0.0
    price_change_1m = round((close_price - open_price) / open_price * 100, 2) if open_price else 0.0
    if close_price > upper:
        emoji, direction, band_val = "⚡️", "выше верхней BB", upper
    else:
        emoji, direction, band_val = "⚡️", "ниже нижней BB", lower
    return Alert(ticker=ticker, short_name=short_name, exchange="MOEX", alert_type="bollinger",
        emoji=emoji, headline=f"Выход {direction} ({sigma}σ)",
        details=f"Цена: {close_price:.2f} ₽ | Полоса: {band_val:.2f} ₽ | Среднее: {mean:.2f} ₽",
        current_price=current_price, volume_1m=int(volume), value_1m=volume*current_price,
        price_change_1m_pct=price_change_1m, price_change_day_pct=price_change_day, price_change_week_pct=weekly_change)


def run_all_detectors(ticker, short_name, candles, marketdata, securities, weekly_change=None,
                     volume_multiplier=3.0, price_threshold_pct=3.0, bollinger_sigma=2.5):
    alerts = []
    for fn, kw in [(detect_volume_spike, {'multiplier': volume_multiplier}),
                   (detect_price_move, {'threshold_pct': price_threshold_pct}),
                   (detect_bollinger_break, {'sigma': bollinger_sigma})]:
        try:
            r = fn(ticker, short_name, candles, marketdata, securities, weekly_change=weekly_change, **kw)
            if r: alerts.append(r)
        except Exception:
            pass
    return alerts
