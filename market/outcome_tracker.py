"""Оценивает движение цены через 15 минут после каждого сигнала."""
from __future__ import annotations

import asyncio
import logging

from database.db import get_pending_outcomes, save_alert_outcome
from market.moex import get_moex_marketdata

logger = logging.getLogger(__name__)


class OutcomeTracker:
    def __init__(self, interval_seconds: int = 60):
        self.interval = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None

    async def _pass(self) -> None:
        pending = get_pending_outcomes()
        if not pending:
            return
        tickers = sorted({row["ticker"] for row in pending})
        market = await get_moex_marketdata(tickers)
        for row in pending:
            current = market.get(row["ticker"], {}).get("LAST")
            initial = row["signal_price"]
            if not current or not initial:
                continue
            outcome = (float(current) - float(initial)) / float(initial) * 100
            save_alert_outcome(row["id"], round(outcome, 4))

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._pass()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Outcome tracker error: %s", exc)
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
