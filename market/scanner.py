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

from market.moex import (
    get_all_moex_tickers,
    get_moex_marketdata,
    get_moex_securities_info,
    get_moex_candles,
    get_weekly_change,
)
from market.anomaly import Alert, AlertCooldownFilter, run_all_detectors

logger = logging.getLogger(__name__)

# ─── Настройки детекторов ────────────────────────────────────────────────────
VOLUME_MULTIPLIER = 3.0
PRICE_THRESHOLD_PCT = 3.0
BOLLINGER_SIGMA = 2.5
CANDLES_N = 30           # количество минутных свечей для анализа
SCAN_BOARD = "TQBR"
TICKER_DELAY = 0.1       # пауза между тикерами (сек)

# ─── Freshness watermark ────────────────────────────────────────────────────
# Максимальный возраст котировки (мс): данные старше — пропускаем
STALE_AFTER_MS = 5_000   # 5 секунд

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

        # Время последнего обновления котировок (monotonic ms) по тикеру
        self._data_received_at: dict[str, float] = {}

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
        self._cycle += 1
        logger.info("[Scan #%d] Старт %s", self._cycle, datetime.now().strftime("%H:%M:%S"))

        # Шаг 1: список всех торгуемых тикеров
        tickers = await get_all_moex_tickers(SCAN_BOARD)
        if not tickers:
            logger.warning("Список тикеров пустой, пропускаем цикл")
            return
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
        raw_alerts: list[Alert] = []
        for ticker in tickers:
            try:
                alerts = await self._scan_ticker(
                    ticker,
                    marketdata_map.get(ticker, {}),
                    securities_map.get(ticker, {}),
                    update_weekly=update_weekly,
                    received_at_ms=self._data_received_at.get(ticker, fetch_time_ms),
                )
                raw_alerts.extend(alerts)
            except Exception as exc:
                logger.debug("Ошибка при сканировании %s: %s", ticker, exc)
            await asyncio.sleep(TICKER_DELAY)

        # Шаг 5: Post-Filter (кулдаун + cross-combining)
        final_alerts = self._post_filter(raw_alerts)

        logger.info("[Scan #%d] Алертов (raw/final): %d / %d", self._cycle, len(raw_alerts), len(final_alerts))

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

        # Шаг 3: Freshness watermark
        # Проверяем, насколько свежи данные, полученные от MOEX ISS
        age_ms = time.monotonic() * 1000 - received_at_ms
        if age_ms > self._stale_after_ms:
            logger.debug("Freshness skip %s: age=%.0fms > stale_after=%.0fms",
                         ticker, age_ms, self._stale_after_ms)
            return []

        short_name = securities.get("SHORTNAME") or ticker

        # Минутные свечи
        candles = await get_moex_candles(ticker, SCAN_BOARD, interval=1, n_bars=CANDLES_N)
        if len(candles) < 5:
            return []

        # Post-filter min_size (быстрая проверка до детекторов)
        last_volume = float(candles[-1].get("volume") or 0)
        if last_volume < self._min_volume_lots:
            return []

        # Weekly change (кеш)
        if update_weekly or ticker not in self._weekly_cache:
            try:
                self._weekly_cache[ticker] = await get_weekly_change(ticker, last_price, SCAN_BOARD)
            except Exception:
                self._weekly_cache[ticker] = None

        weekly_change = self._weekly_cache.get(ticker)

        # Шаг 4: Detectors
        alerts = run_all_detectors(
            ticker=ticker,
            short_name=short_name,
            candles=candles,
            marketdata=marketdata,
            securities=securities,
            weekly_change=weekly_change,
            volume_multiplier=VOLUME_MULTIPLIER,
            price_threshold_pct=PRICE_THRESHOLD_PCT,
            bollinger_sigma=BOLLINGER_SIGMA,
        )

        # Проставляем метку received_at_ms в каждый алерт
        for a in alerts:
            a.received_at_ms = received_at_ms

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
