"""
Сканер рынка: динамически получает все акции MOEX TQBR,
собирает рыночные данные пакетно и запускает детекторы аномалий.

Архитектура пайплайна (5 шагов):
  1. Ingestion          — получение котировок и свечей с MOEX ISS
  2. Pre-Filter         — Freshness watermark (stale_after_ms), min_volume
  3. Detectors          — robust_z (медиана+IQR), price-confirmation
  4. Post-Filter        — min_size, кулдаун, кросс-детектор combining
  5. Dispatch           — вызов callback с финальным списком алертов
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from config import (
    VOLUME_SPIKE_MULTIPLIER,
    PRICE_CHANGE_THRESHOLD_PCT,
    VOLATILITY_SIGMA,
)

from market.moex import (
    get_all_moex_tickers,
    get_moex_marketdata,
    get_moex_securities_info,
    get_moex_candles,
    get_weekly_change,
)
from market.anomaly import Alert, AlertCooldownFilter, run_all_detectors
from health_state import update_health

logger = logging.getLogger(__name__)

# ─── Настройки детекторов ────────────────────────────────────────────────────
VOLUME_MULTIPLIER = VOLUME_SPIKE_MULTIPLIER
PRICE_THRESHOLD_PCT = PRICE_CHANGE_THRESHOLD_PCT
BOLLINGER_SIGMA = VOLATILITY_SIGMA
CANDLES_N = 30           # количество минутных свечей для анализа
SCAN_BOARD = "TQBR"
MAX_CONCURRENT_REQUESTS = 8

# ─── Freshness watermark ────────────────────────────────────────────────────
# Максимальный возраст котировки (мс): данные старше — пропускаем
STALE_AFTER_MS = 120_000  # полный проход по рынку может занимать больше минуты

# ─── Post-filter: минимальный размер ────────────────────────────────────────
# Алерт игнорируется, если объём < MIN_VOLUME_LOTS лотов
MIN_VOLUME_LOTS = 1


class MarketScanner:
    """
    Асинхронный сканер рынка.

    Шаги пайплайна:
    1. Раз в N секунд получает список всех торгуемых акций TQBR.
    2. Пакетно запрашивает котировки (LAST, CHANGE, VOLTODAY и др.).
    3. Pre-Filter: проверяет freshness (stale_after_ms) каждой котировки.
    4. Для каждого тикера запрашивает минутные свечи → детекторы аномалий.
    5. Post-Filter: кулдаун (AlertCooldownFilter), min_size, cross-combining.
    6. Dispatch: вызывает callback с финальными алертами.
    """

    def __init__(
        self,
        alert_callback,            # async def callback(alerts: list[Alert])
        interval_seconds: int = 60,
        weekly_change_every: int = 5,  # обновлять weekly change каждые N циклов
        cooldown_seconds: float = 60,  # кулдаун между алертами по (ticker, type)
        stale_after_ms: float = STALE_AFTER_MS,
        min_volume_lots: int = MIN_VOLUME_LOTS,
        health_callback=None,
    ):
        self.alert_callback = alert_callback
        self.interval = interval_seconds
        self._weekly_every = weekly_change_every
        self._cycle = 0
        self._weekly_cache: dict[str, Optional[float]] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Post-filter: кулдаун
        self._cooldown_filter = AlertCooldownFilter(cooldown_seconds=cooldown_seconds)

        # Pre-filter: freshness
        self._stale_after_ms = stale_after_ms
        self._min_volume_lots = min_volume_lots
        self._health_callback = health_callback
        self._consecutive_failures = 0

        # Время последнего обновления котировок (monotonic ms) по тикеру
        self._data_received_at: dict[str, float] = {}
        self._request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Сканер рынка запущен (интервал %d сек)", self.interval)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("Сканер рынка остановлен")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._scan()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Ошибка в цикле сканера: %s", exc)
            await asyncio.sleep(self.interval)

    # ── Шаги 1–2: Ingestion ──────────────────────────────────────────────────

    async def _scan(self) -> None:
        scan_started = time.monotonic()
        self._cycle += 1
        update_health(last_scan_at=time.time())
        logger.info("[Scan #%d] Старт %s", self._cycle, datetime.now().strftime("%H:%M:%S"))

        # Шаг 1: список всех торгуемых тикеров
        tickers = await get_all_moex_tickers(SCAN_BOARD)
        if not tickers:
            self._consecutive_failures += 1
            update_health(
                consecutive_failures=self._consecutive_failures,
                last_error="MOEX вернул пустой список тикеров",
            )
            logger.warning("Список тикеров пустой, пропускаем цикл")
            if self._health_callback and self._consecutive_failures in (3, 10):
                await self._health_callback(
                    f"MOEX недоступен: {self._consecutive_failures} неудачных циклов подряд"
                )
            return
        recovered = self._consecutive_failures >= 3
        self._consecutive_failures = 0
        logger.info("[Scan #%d] Тикеров: %d", self._cycle, len(tickers))

        # Шаг 2: пакетный запрос котировок
        fetch_time_ms = time.monotonic() * 1000
        marketdata_map = await get_moex_marketdata(tickers, SCAN_BOARD)
        securities_map = await get_moex_securities_info(tickers, SCAN_BOARD)

        # Проставляем метку времени получения данных для Freshness-фильтра
        for t in tickers:
            self._data_received_at[t] = fetch_time_ms

        update_weekly = (self._cycle % self._weekly_every == 1)

        # Шаги 3–4: Pre-Filter + Detectors
        async def scan_one(ticker: str) -> list[Alert]:
            try:
                async with self._request_semaphore:
                    return await self._scan_ticker(
                        ticker,
                        marketdata_map.get(ticker, {}),
                        securities_map.get(ticker, {}),
                        update_weekly=update_weekly,
                        received_at_ms=self._data_received_at.get(ticker, fetch_time_ms),
                    )
            except Exception as exc:
                logger.warning("Ошибка при сканировании %s: %s", ticker, exc)
                return []

        results = await asyncio.gather(*(scan_one(ticker) for ticker in tickers))
        raw_alerts = [alert for ticker_alerts in results for alert in ticker_alerts]

        # Шаг 5: Post-Filter (кулдаун + cross-combining)
        final_alerts = self._post_filter(raw_alerts)

        logger.info("[Scan #%d] Алертов (raw/final): %d / %d", self._cycle, len(raw_alerts), len(final_alerts))
        duration = round(time.monotonic() - scan_started, 2)
        update_health(
            last_success_at=time.time(),
            ticker_count=len(tickers),
            scan_duration_seconds=duration,
            consecutive_failures=0,
            last_error=None,
        )
        if recovered and self._health_callback:
            await self._health_callback(f"MOEX снова доступен: получено {len(tickers)} тикеров")

        # Шаг 6: Dispatch
        if final_alerts and self.alert_callback:
            await self.alert_callback(final_alerts)

    # ── Шаг 3: Pre-Filter + Шаг 4: Detectors ────────────────────────────────

    async def _scan_ticker(
        self,
        ticker: str,
        marketdata: dict,
        securities: dict,
        update_weekly: bool = False,
        received_at_ms: float = 0.0,
    ) -> list[Alert]:

        last_price = marketdata.get("LAST")
        if not last_price:
            return []
        last_price = float(last_price)

        short_name = securities.get("SHORTNAME") or ticker

        # Минутные свечи
        candles = await get_moex_candles(ticker, SCAN_BOARD, interval=1, n_bars=CANDLES_N)
        if len(candles) < 5:
            return []

        # Post-filter min_size (быстрая проверка до детекторов)
        last_volume = float(candles[-1].get("volume") or 0)
        if last_volume < self._min_volume_lots:
            return []

        # Шаг 4: Detectors
        alerts = run_all_detectors(
            ticker=ticker,
            short_name=short_name,
            candles=candles,
            marketdata=marketdata,
            securities=securities,
            weekly_change=self._weekly_cache.get(ticker),
            volume_multiplier=VOLUME_MULTIPLIER,
            price_threshold_pct=PRICE_THRESHOLD_PCT,
            bollinger_sigma=BOLLINGER_SIGMA,
        )

        # Недельную динамику загружаем только для реально сработавшего сигнала.
        if alerts and (update_weekly or ticker not in self._weekly_cache):
            try:
                self._weekly_cache[ticker] = await get_weekly_change(ticker, last_price, SCAN_BOARD)
            except Exception as exc:
                logger.debug("Не удалось получить недельную динамику %s: %s", ticker, exc)
                self._weekly_cache[ticker] = None

        completed_at_ms = time.monotonic() * 1000
        for a in alerts:
            a.received_at_ms = completed_at_ms
            a.price_change_week_pct = self._weekly_cache.get(ticker)

        return alerts

    # ── Шаг 5: Post-Filter ───────────────────────────────────────────────────

    def _post_filter(self, alerts: list[Alert]) -> list[Alert]:
        """
        Post-Filter:
          1. min_size — отсеиваем совсем мелкие (на случай если детектор пропустил)
          2. Freshness — на случай задержки между получением данных и детектором
          3. cross-combining — volume_spike + bollinger/price_move → composite
          4. cooldown — блокируем повторные алерты по (ticker, type)
        """
        # 1+2: min_size + freshness
        filtered: list[Alert] = []
        for a in alerts:
            if a.volume_1m < self._min_volume_lots:
                continue
            if not a.is_fresh(stale_after_ms=self._stale_after_ms):
                logger.debug("Post-filter stale alert: %s/%s", a.ticker, a.alert_type)
                continue
            filtered.append(a)

        # 3: cross-combining
        combined = self._cooldown_filter.merge_cross_alerts(filtered)

        # 4: cooldown
        final = [a for a in combined if self._cooldown_filter.allow(a)]

        return final
