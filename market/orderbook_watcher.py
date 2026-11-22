"""
Монитор стакана заявок (Level 2).

Архитектура:
  - Крупные заявки: абсолютный порог ИЛИ относительный (N × медиана стакана)
  - Фильтр «мёртвых» заявок: игнорируем позиции далеко от лучшего бид/аск
  - Детектор айсберг-паттернов: мониторинг цикла исполнение→пополнение, K-счётчик
  - Кулдаун: 60 сек между алертами по одному инструменту
"""
from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Coroutine, Optional

import aiohttp

# ─── Настройки ───────────────────────────────────────────────────────────────

MOEX_ORDERBOOK_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/"
    "securities/{ticker}/orderbook.json?depth=20"
)

# Крупная заявка: не менее X × медиана по стакану
LARGE_ORDER_RELATIVE_MULTIPLIER = 7.0

# Крупная заявка: абсолютный порог в рублях (страховка)
MIN_ORDER_VALUE_RUB = 5_000_000

# Фильтр «мёртвых»: максимальное расстояние от лучшей цены (в % от mid-price)
DEAD_ORDER_SPREAD_PCT = 2.0

# Айсберг: минимальное число циклов исполнение→пополнение для сигнала
ICEBERG_K_THRESHOLD = 3

# Кулдаун между алертами по одному инструменту (сек)
ALERT_COOLDOWN_SECONDS = 60

# Период опроса (сек)
POLL_INTERVAL_SECONDS = 5


@dataclass
class LargeOrderAlert:
    ticker: str
    short_name: str
    side: str                     # "bid" | "ask"
    price: float
    quantity: int                 # лотов
    value_rub: float
    median_order_value: float     # медиана по стакану (руб)
    relative_size: float          # quantity / median (множитель)
    alert_time: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))
    alert_type: str = "large_order"

    def format_message(self) -> str:
        side_label = "🟢 Покупка (bid)" if self.side == "bid" else "🔴 Продажа (ask)"
        return (
            f"🏔 <b>{self.ticker}</b> — крупная заявка в стакане\n"
            f"🕐 {self.alert_time}\n"
            f"\n"
            f"{side_label}\n"
            f"💰 Цена: <b>{self.price:,.2f} ₽</b>\n"
            f"📦 Объём: <b>{self.quantity:,} лот</b> "
            f"({self.value_rub / 1_000_000:.2f}M ₽)\n"
            f"📊 Медиана стакана: {self.median_order_value / 1_000_000:.2f}M ₽\n"
            f"⚡️ В <b>{self.relative_size:.1f}×</b> больше нормы"
        )


@dataclass
class IcebergAlert:
    ticker: str
    short_name: str
    side: str                       # "bid" | "ask"
    price: float
    k_count: int                    # число подтверждённых циклов
    total_executed_lots: int        # суммарный исполненный объём
    alert_time: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))
    alert_type: str = "iceberg"

    def format_message(self) -> str:
        side_label = "🟢 на покупку" if self.side == "bid" else "🔴 на продажу"
        return (
            f"🧊 <b>{self.ticker}</b> — айсберг-заявка\n"
            f"🕐 {self.alert_time}\n"
            f"\n"
            f"Паттерн {side_label} @ <b>{self.price:,.2f} ₽</b>\n"
            f"🔄 Циклов пополнения: <b>{self.k_count}</b>\n"
            f"📦 Суммарно исполнено: <b>{self.total_executed_lots:,} лот</b>\n"
            f"💡 Крупный участник скрывает реальный объём"
        )


# ─── Вспомогательные функции ─────────────────────────────────────────────────

def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _mid_price(bids: list[dict], asks: list[dict]) -> float:
    """Mid-price из лучшего бид и аск."""
    best_bid = _safe_float(bids[0].get("PRICE")) if bids else 0.0
    best_ask = _safe_float(asks[0].get("PRICE")) if asks else 0.0
    if best_bid and best_ask:
        return (best_bid + best_ask) / 2
    return best_bid or best_ask


def _filter_dead_orders(
    orders: list[dict],
    mid: float,
    spread_pct: float = DEAD_ORDER_SPREAD_PCT,
) -> list[dict]:
    """
    Удаляет «мёртвые» заявки, стоящие далеко от рыночной цены.
    Шаг Pre-Filter (freshness/proximity) для стакана.
    """
    if mid == 0:
        return orders
    threshold = mid * spread_pct / 100
    return [o for o in orders if abs(_safe_float(o.get("PRICE")) - mid) <= threshold]


def _compute_median_order_value(orders: list[dict], price_fallback: float) -> float:
    """
    Медиана стоимости заявок в рублях (Шаг 2: робастная статистика).
    """
    values = []
    for o in orders:
        price = _safe_float(o.get("PRICE")) or price_fallback
        qty = _safe_float(o.get("QUANTITY"))
        if price > 0 and qty > 0:
            values.append(price * qty)
    if not values:
        return 0.0
    return statistics.median(values)


# ─── Трекер айсберг-паттернов ────────────────────────────────────────────────

@dataclass
class _IcebergLevel:
    """Состояние отслеживания одного ценового уровня на предмет айсберга."""
    price: float
    side: str
    last_qty: int = 0
    k_count: int = 0
    total_executed: int = 0
    last_seen_ts: float = field(default_factory=time.monotonic)

    def update(self, current_qty: int) -> bool:
        """
        Обновляет счётчик. Возвращает True, если цикл подтверждён
        (объём уменьшился, потом восстановился — признак пополнения).
        """
        self.last_seen_ts = time.monotonic()
        if self.last_qty == 0:
            self.last_qty = current_qty
            return False

        delta = current_qty - self.last_qty
        if delta < 0:
            # Объём уменьшился — исполняется
            self.total_executed += abs(delta)
        elif delta > 0 and self.total_executed > 0:
            # Объём восстановился после исполнения — пополнение!
            self.k_count += 1
            self.total_executed = 0  # сбрасываем для следующего цикла
            self.last_qty = current_qty
            return True

        self.last_qty = current_qty
        return False

    def is_stale(self, ttl_seconds: float = 120.0) -> bool:
        return time.monotonic() - self.last_seen_ts > ttl_seconds


class IcebergTracker:
    """
    Отслеживает айсберг-паттерны по всем уровням стакана.
    Шаг 3 по архитектуре: Iceberg detection.
    """
    def __init__(self, k_threshold: int = ICEBERG_K_THRESHOLD):
        self._k_threshold = k_threshold
        # (ticker, side, price) → _IcebergLevel
        self._levels: dict[tuple, _IcebergLevel] = {}

    def update(
        self,
        ticker: str,
        bids: list[dict],
        asks: list[dict],
    ) -> list[tuple[str, float, int, int]]:
        """
        Обновляет трекер; возвращает список (side, price, k_count, total_lots)
        для уровней, достигших порога K.
        """
        triggered: list[tuple[str, float, int, int]] = []
        self._cleanup_stale(ticker)

        for side, orders in (("bid", bids), ("ask", asks)):
            for o in orders:
                price = _safe_float(o.get("PRICE"))
                qty = int(_safe_float(o.get("QUANTITY")))
                if price <= 0 or qty <= 0:
                    continue
                key = (ticker, side, round(price, 4))
                if key not in self._levels:
                    self._levels[key] = _IcebergLevel(price=price, side=side, last_qty=qty)
                else:
                    level = self._levels[key]
                    if level.update(qty) and level.k_count >= self._k_threshold:
                        triggered.append((side, price, level.k_count, level.total_executed))
                        # Сбрасываем счётчик, чтобы не спамить
                        level.k_count = 0

        return triggered

    def _cleanup_stale(self, ticker: str) -> None:
        stale = [k for k, v in self._levels.items() if k[0] == ticker and v.is_stale()]
        for k in stale:
            del self._levels[k]


# ─── OrderbookWatcher ────────────────────────────────────────────────────────

class OrderbookWatcher:
    """
    Асинхронный монитор стакана.
    Детектирует крупные заявки и айсберг-паттерны.
    """

    def __init__(
        self,
        tickers: list[tuple[str, str]],   # [(ticker, short_name), ...]
        alert_callback: Callable[..., Coroutine],
        large_multiplier: float = LARGE_ORDER_RELATIVE_MULTIPLIER,
        min_order_value_rub: float = MIN_ORDER_VALUE_RUB,
        dead_spread_pct: float = DEAD_ORDER_SPREAD_PCT,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        cooldown_seconds: float = ALERT_COOLDOWN_SECONDS,
    ):
        self._tickers = tickers
        self._callback = alert_callback
        self._large_multiplier = large_multiplier
        self._min_order_value = min_order_value_rub
        self._dead_spread_pct = dead_spread_pct
        self._poll_interval = poll_interval
        self._cooldown = cooldown_seconds

        self._iceberg_tracker = IcebergTracker()
        self._last_alert: dict[str, float] = {}   # ticker → monotonic ts

    def _is_cooled(self, ticker: str) -> bool:
        last = self._last_alert.get(ticker, 0.0)
        return time.monotonic() - last >= self._cooldown

    def _mark_alerted(self, ticker: str) -> None:
        self._last_alert[ticker] = time.monotonic()

    async def _fetch_orderbook(self, session: aiohttp.ClientSession, ticker: str) -> dict:
        url = MOEX_ORDERBOOK_URL.format(ticker=ticker)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return await resp.json(content_type=None)

    def _parse_orders(self, raw: dict) -> tuple[list[dict], list[dict]]:
        """Возвращает (bids, asks) как списки словарей."""
        try:
            ob = raw.get("orderbook", {})
            columns = ob.get("columns", [])
            data = ob.get("data", [])
            rows = [dict(zip(columns, row)) for row in data]
            bids = [r for r in rows if r.get("BUYSELL") == "B"]
            asks = [r for r in rows if r.get("BUYSELL") == "S"]
            return bids, asks
        except Exception:
            return [], []

    async def _process_ticker(
        self,
        session: aiohttp.ClientSession,
        ticker: str,
        short_name: str,
    ) -> None:
        try:
            raw = await self._fetch_orderbook(session, ticker)
        except Exception:
            return

        bids, asks = self._parse_orders(raw)
        if not bids and not asks:
            return

        mid = _mid_price(bids, asks)

        # Шаг Pre-Filter: убираем заявки далеко от рынка
        bids = _filter_dead_orders(bids, mid, self._dead_spread_pct)
        asks = _filter_dead_orders(asks, mid, self._dead_spread_pct)

        all_orders = bids + asks

        # Робастная медиана стоимости заявок
        median_val = _compute_median_order_value(all_orders, mid)

        # ── Детектор крупных заявок ──
        if self._is_cooled(ticker):
            for side, orders in (("bid", bids), ("ask", asks)):
                for o in orders:
                    price = _safe_float(o.get("PRICE"))
                    qty = int(_safe_float(o.get("QUANTITY")))
                    if price <= 0 or qty <= 0:
                        continue
                    value = price * qty
                    relative = (value / median_val) if median_val > 0 else 0

                    if value >= self._min_order_value and relative >= self._large_multiplier:
                        alert = LargeOrderAlert(
                            ticker=ticker,
                            short_name=short_name,
                            side=side,
                            price=price,
                            quantity=qty,
                            value_rub=value,
                            median_order_value=median_val,
                            relative_size=relative,
                        )
                        self._mark_alerted(ticker)
                        await self._callback(alert)
                        break  # один алерт за цикл по инструменту
                else:
                    continue
                break

        # ── Детектор айсберга ──
        triggered = self._iceberg_tracker.update(ticker, bids, asks)
        for side, price, k_count, total_lots in triggered:
            if self._is_cooled(ticker):
                iceberg = IcebergAlert(
                    ticker=ticker,
                    short_name=short_name,
                    side=side,
                    price=price,
                    k_count=k_count,
                    total_executed_lots=total_lots,
                )
                self._mark_alerted(ticker)
                await self._callback(iceberg)

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                tasks = [
                    self._process_ticker(session, ticker, name)
                    for ticker, name in self._tickers
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(self._poll_interval)
