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
import logging
import ssl
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Callable, Coroutine, Optional

import aiohttp
import certifi

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

# ─── Настройки ───────────────────────────────────────────────────────────────

MOEX_ORDERBOOK_URL = (
    "http://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/"
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
    alert_time: str = field(default_factory=lambda: datetime.now(MSK).strftime("%H:%M:%S"))
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
    alert_time: str = field(default_factory=lambda: datetime.now(MSK).strftime("%H:%M:%S"))
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



# ─── Внутренние типы ───────────────────────────────────────────────────────────

@dataclass
class _OrderEntry:
    """Один уровень стакана."""
    price: float
    quantity: int   # лотов

    @property
    def value_rub(self) -> float:
        return self.price * self.quantity


@dataclass
class _IcebergState:
    """Состояние отслеживаемого уровня."""
    side: str
    price: float
    visible_qty: int           # количество в последнем снимке
    total_executed: int = 0    # суммарный исполненный объём
    k_count: int = 0           # число циклов исполнение→пополнение


AlertCallback = Callable[
    [LargeOrderAlert | IcebergAlert],
    Coroutine[None, None, None],
]


# ─── Помощные функции ──────────────────────────────────────────────────────────────


def _parse_orderbook(raw: dict) -> tuple[list[_OrderEntry], list[_OrderEntry]]:
    """Парсим MOEX ISS ответ стакана.

    Returns:
        (bids, asks) — отсортированы по цене (покупки — по убыванию, продажи — по возрастанию)
    """
    try:
        ob_data = raw["orderbook"]
        columns = ob_data["columns"]
        rows = ob_data["data"]
    except (KeyError, TypeError):
        return [], []

    buysell_idx = columns.index("BUYSELL")
    price_idx = columns.index("PRICE")
    qty_idx = columns.index("QUANTITY")

    bids: list[_OrderEntry] = []
    asks: list[_OrderEntry] = []

    for row in rows:
        try:
            side = row[buysell_idx]
            price = float(row[price_idx])
            qty = int(row[qty_idx])
        except (TypeError, ValueError, IndexError):
            continue
        entry = _OrderEntry(price=price, quantity=qty)
        if side == "B":
            bids.append(entry)
        elif side == "S":
            asks.append(entry)

    bids.sort(key=lambda e: e.price, reverse=True)
    asks.sort(key=lambda e: e.price)
    return bids, asks


def _median_value(entries: list[_OrderEntry]) -> float:
    """Median order value in RUB across both sides."""
    vals = [e.value_rub for e in entries if e.quantity > 0]
    if not vals:
        return 0.0
    return statistics.median(vals)


def _is_dead_order(price: float, best_bid: float, best_ask: float) -> bool:
    """True if the order is far from NBBO (probably never going to execute)."""
    if best_bid <= 0 or best_ask <= 0:
        return False
    mid = (best_bid + best_ask) / 2
    return abs(price - mid) / mid * 100 > DEAD_ORDER_SPREAD_PCT


# ─── Основной класс ──────────────────────────────────────────────────────────────────


class OrderbookWatcher:
    """Watching Level-2 for a list of MOEX tickers.

    Args:
        tickers:        List of (ticker, short_name) pairs.
        alert_callback: Async callable that receives LargeOrderAlert or IcebergAlert.
        poll_interval:  Seconds between ISS requests per ticker.
    """

    def __init__(
        self,
        tickers: list[tuple[str, str]],
        alert_callback: AlertCallback,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self.tickers = tickers
        self.alert_callback = alert_callback
        self.poll_interval = poll_interval

        # Состояние айсбергов: ticker -> price_key -> _IcebergState
        self._iceberg: dict[str, dict[str, _IcebergState]] = {}

        # Кулдаун: ticker -> last_alert_time
        self._last_alert: dict[str, float] = {}

        self._session: Optional[aiohttp.ClientSession] = None
        self._last_check_utc: Optional[datetime] = None

        self._running: bool = False
        self._task: asyncio.Task | None = None

    async def _fetch_orderbook(self, session: aiohttp.ClientSession, ticker: str) -> dict:
        url = MOEX_ORDERBOOK_URL.format(ticker=ticker)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    def _check_large_orders(
        self,
        ticker: str,
        short_name: str,
        bids: list[_OrderEntry],
        asks: list[_OrderEntry],
    ) -> list[LargeOrderAlert]:
        alerts: list[LargeOrderAlert] = []
        all_entries = bids + asks
        if not all_entries:
            return alerts

        median_val = _median_value(all_entries)
        best_bid = bids[0].price if bids else 0.0
        best_ask = asks[0].price if asks else 0.0

        for side, entries in (("bid", bids), ("ask", asks)):
            for entry in entries:
                if _is_dead_order(entry.price, best_bid, best_ask):
                    continue
                if entry.value_rub < MIN_ORDER_VALUE_RUB:
                    continue
                if median_val > 0 and entry.value_rub < median_val * LARGE_ORDER_RELATIVE_MULTIPLIER:
                    continue
                rel = entry.value_rub / median_val if median_val > 0 else 0.0
                alerts.append(LargeOrderAlert(
                    ticker=ticker,
                    short_name=short_name,
                    side=side,
                    price=entry.price,
                    quantity=entry.quantity,
                    value_rub=entry.value_rub,
                    median_order_value=median_val,
                    relative_size=rel,
                ))
        return alerts

    def _update_iceberg(
        self,
        ticker: str,
        short_name: str,
        bids: list[_OrderEntry],
        asks: list[_OrderEntry],
    ) -> list[IcebergAlert]:
        alerts: list[IcebergAlert] = []
        ticker_state = self._iceberg.setdefault(ticker, {})

        for side, entries in (("bid", bids), ("ask", asks)):
            current_prices = {e.price for e in entries}

            # Очистить устаревшие уровни
            stale = [
                k for k, s in ticker_state.items()
                if s.side == side and s.price not in current_prices
            ]
            for k in stale:
                del ticker_state[k]

            for entry in entries:
                key = f"{side}_{entry.price}"
                state = ticker_state.get(key)

                if state is None:
                    ticker_state[key] = _IcebergState(
                        side=side,
                        price=entry.price,
                        visible_qty=entry.quantity,
                    )
                    continue

                prev_qty = state.visible_qty
                curr_qty = entry.quantity

                # Цикл: количество упало (исполнение) и затем поднялось (пополнение)
                if prev_qty > curr_qty and curr_qty > prev_qty * 0.5:
                    # Простое уменьшение — не айсберг
                    state.visible_qty = curr_qty
                    continue
                if curr_qty > prev_qty and prev_qty < curr_qty * 0.9:
                    executed = curr_qty - prev_qty  # оценка
                    state.total_executed += abs(executed)
                    state.k_count += 1
                    state.visible_qty = curr_qty

                    if state.k_count >= ICEBERG_K_THRESHOLD:
                        alerts.append(IcebergAlert(
                            ticker=ticker,
                            short_name=short_name,
                            side=side,
                            price=entry.price,
                            k_count=state.k_count,
                            total_executed_lots=state.total_executed,
                        ))
                        # Сброс счётчика после срабатывания
                        state.k_count = 0
                        state.total_executed = 0
                else:
                    state.visible_qty = curr_qty

        return alerts

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

        bids, asks = _parse_orderbook(raw)
        if not bids and not asks:
            return

        now = time.monotonic()
        last = self._last_alert.get(ticker, 0.0)
        in_cooldown = (now - last) < ALERT_COOLDOWN_SECONDS

        candidates: list[LargeOrderOrderAlert | IcebergAlert] = []
        candidates += self._check_large_orders(ticker, short_name, bids, asks)
        candidates += self._update_iceberg(ticker, short_name, bids, asks)

        if candidates and not in_cooldown:
            self._last_alert[ticker] = now
            for alert in candidates:
                try:
                    await self.alert_callback(alert)
                except Exception:
                    pass

    async def run(self) -> None:
        """Main polling loop. Запускается через start()."""
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_context, limit=10)
        async with aiohttp.ClientSession(connector=connector) as session:
            self._session = session
            while self._running:
                tasks = [
                    self._process_ticker(session, ticker, short_name)
                    for ticker, short_name in self.tickers
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                failures = sum(isinstance(result, Exception) for result in results)
                if failures:
                    logger.warning("Orderbook: %d/%d запросов завершились ошибкой", failures, len(tasks))
                self._last_check_utc = datetime.utcnow()
                await asyncio.sleep(self.poll_interval)

    async def start(self) -> None:
        """Запускает watcher как фоновую задачу asyncio."""
        self._running = True
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        """Останавливает watcher."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
