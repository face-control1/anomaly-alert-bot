"""
Сканер рынка: динамически получает все акции MOEX TQBR,
собирает рыночные данные пакетно и запускает детекторы аномалий.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from market.moex import (
    get_all_moex_tickers,
    get_moex_marketdata,
    get_moex_securities_info,
    get_moex_candles,
    get_weekly_change,
)
from market.anomaly import Alert, run_all_detectors

logger = logging.getLogger(__name__)

VOLUME_MULTIPLIER = 3.0
PRICE_THRESHOLD_PCT = 3.0
BOLLINGER_SIGMA = 2.5
CANDLES_N = 30
SCAN_BOARD = "TQBR"
TICKER_DELAY = 0.1


class MarketScanner:
    def __init__(self, alert_callback, interval_seconds=60, weekly_change_every=5):
        self.alert_callback = alert_callback
        self.interval = interval_seconds
        self._weekly_every = weekly_change_every
        self._cycle = 0
        self._weekly_cache: dict[str, Optional[float]] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Сканер рынка запущен (интервал %d сек)", self.interval)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._scan()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Ошибка в цикле сканера: %s", exc)
            await asyncio.sleep(self.interval)

    async def _scan(self) -> None:
        self._cycle += 1
        logger.info("[Scan #%d] Старт %s", self._cycle, datetime.now().strftime("%H:%M:%S"))

        tickers = await get_all_moex_tickers(SCAN_BOARD)
        if not tickers:
            logger.warning("Список тикеров пустой")
            return
        logger.info("[Scan #%d] Тикеров: %d", self._cycle, len(tickers))

        marketdata_map = await get_moex_marketdata(tickers, SCAN_BOARD)
        securities_map = await get_moex_securities_info(tickers, SCAN_BOARD)
        update_weekly = (self._cycle % self._weekly_every == 1)

        all_alerts: list[Alert] = []
        for ticker in tickers:
            try:
                alerts = await self._scan_ticker(
                    ticker, marketdata_map.get(ticker, {}),
                    securities_map.get(ticker, {}), update_weekly=update_weekly,
                )
                all_alerts.extend(alerts)
            except Exception as exc:
                logger.debug("Ошибка %s: %s", ticker, exc)
            await asyncio.sleep(TICKER_DELAY)

        logger.info("[Scan #%d] Алертов: %d", self._cycle, len(all_alerts))
        if all_alerts and self.alert_callback:
            await self.alert_callback(all_alerts)

    async def _scan_ticker(self, ticker, marketdata, securities, update_weekly=False):
        last_price = marketdata.get("LAST")
        if not last_price:
            return []
        last_price = float(last_price)
        short_name = securities.get("SHORTNAME") or ticker

        candles = await get_moex_candles(ticker, SCAN_BOARD, interval=1, n_bars=CANDLES_N)
        if len(candles) < 5:
            return []

        if update_weekly or ticker not in self._weekly_cache:
            try:
                self._weekly_cache[ticker] = await get_weekly_change(ticker, last_price, SCAN_BOARD)
            except Exception:
                self._weekly_cache[ticker] = None

        return run_all_detectors(
            ticker=ticker, short_name=short_name, candles=candles,
            marketdata=marketdata, securities=securities,
            weekly_change=self._weekly_cache.get(ticker),
            volume_multiplier=VOLUME_MULTIPLIER,
            price_threshold_pct=PRICE_THRESHOLD_PCT,
            bollinger_sigma=BOLLINGER_SIGMA,
        )
