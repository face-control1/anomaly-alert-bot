"""
Детектор крупных лимитных заявок в стакане MOEX.
Использует MOEX ISS API: /iss/engines/stock/markets/shares/boards/TQBR/orderbook.json
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional
import aiohttp

logger = logging.getLogger(__name__)

ORDERBOOK_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/{market}"
    "/boards/{board}/securities/{ticker}/orderbook.json"
    "?iss.meta=off&iss.only=orderbook"
)

# Порог: заявка считается крупной, если >= N × средний размер заявки в стакане
LARGE_ORDER_MULTIPLIER = 5.0
# Минимальное абсолютное значение заявки в рублях
MIN_ORDER_VALUE_RUB = 5_000_000  # 5 млн руб.

MARKETS = [
    ("stock", "TQBR"),   # акции
    ("stock", "TQTF"),   # ETF
]


@dataclass
class LargeOrderAlert:
    ticker: str
    exchange: str
    side: str           # "BID" или "ASK"
    price: float
    quantity: int
    value_rub: float
    avg_order_value: float
    multiplier: float
    board: str


class OrderbookWatcher:
    def __init__(
        self,
        tickers: list[dict],  # [{"ticker": "SBER", "exchange": "MOEX", "board": "TQBR"}]
        alert_callback: Callable[[LargeOrderAlert], Awaitable[None]],
        interval_seconds: int = 60,
    ):
        self.tickers = tickers
        self.alert_callback = alert_callback
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # Кеш уже отправленных алертов: (ticker, side, price) -> timestamp
        self._sent_alerts: dict[tuple, float] = {}
        self._alert_cooldown = 300  # 5 минут

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("OrderbookWatcher started, %d tickers", len(self.tickers))

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        while self._running:
            try:
                await self._scan_all()
            except Exception as e:
                logger.error("OrderbookWatcher error: %s", e)
            await asyncio.sleep(self.interval_seconds)

    async def _scan_all(self):
        async with aiohttp.ClientSession() as session:
            tasks = [
                self._scan_ticker(session, t["ticker"], t.get("exchange", "MOEX"), t.get("board", "TQBR"))
                for t in self.tickers
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.debug("Orderbook scan error: %s", r)

    async def _scan_ticker(self, session: aiohttp.ClientSession, ticker: str, exchange: str, board: str):
        market = "stock"
        url = ORDERBOOK_URL.format(market=market, board=board, ticker=ticker)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return
                data = await resp.json(content_type=None)
        except Exception as e:
            logger.debug("Orderbook fetch %s: %s", ticker, e)
            return

        try:
            ob = data.get("orderbook", {})
            columns = ob.get("columns", [])
            rows = ob.get("data", [])
            if not columns or not rows:
                return

            col = {name: i for i, name in enumerate(columns)}
            price_idx = col.get("PRICE")
            qty_idx = col.get("QUANTITY")
            buysell_idx = col.get("BUYSELL")

            if price_idx is None or qty_idx is None or buysell_idx is None:
                return

            bids = [(r[price_idx], r[qty_idx]) for r in rows if r[buysell_idx] == "B" and r[qty_idx]]
            asks = [(r[price_idx], r[qty_idx]) for r in rows if r[buysell_idx] == "S" and r[qty_idx]]

            await self._check_side(ticker, exchange, board, bids, "BID")
            await self._check_side(ticker, exchange, board, asks, "ASK")

        except Exception as e:
            logger.debug("Orderbook parse %s: %s", ticker, e)

    async def _check_side(self, ticker: str, exchange: str, board: str, orders: list, side: str):
        if len(orders) < 3:
            return

        values = [p * q for p, q in orders]
        avg_value = sum(values) / len(values)
        if avg_value <= 0:
            return

        for (price, qty), value in zip(orders, values):
            if value < MIN_ORDER_VALUE_RUB:
                continue
            multiplier = value / avg_value
            if multiplier < LARGE_ORDER_MULTIPLIER:
                continue

            # Проверяем кулдаун
            cache_key = (ticker, side, round(price, 2))
            now = asyncio.get_event_loop().time()
            if now - self._sent_alerts.get(cache_key, 0) < self._alert_cooldown:
                continue

            self._sent_alerts[cache_key] = now

            alert = LargeOrderAlert(
                ticker=ticker,
                exchange=exchange,
                side=side,
                price=price,
                quantity=qty,
                value_rub=value,
                avg_order_value=avg_value,
                multiplier=multiplier,
                board=board,
            )
            try:
                await self.alert_callback(alert)
            except Exception as e:
                logger.error("Orderbook alert callback error: %s", e)
