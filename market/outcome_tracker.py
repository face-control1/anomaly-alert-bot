"""Оценивает движение цены через 5, 15, 30 и 60 минут после сигнала."""
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
        pending_by_horizon = {minutes: get_pending_outcomes(minutes) for minutes in (5, 15, 30, 60)}
        pending = [row for rows in pending_by_horizon.values() for row in rows]
        if not pending:
            return
        tickers = sorted({row["ticker"] for row in pending})
        market = await get_moex_marketdata(tickers)
        for minutes, rows in pending_by_horizon.items():
            for row in rows:
                current = market.get(row["ticker"], {}).get("LAST")
                initial = row["signal_price"]
                if not current or not initial:
                    continue
                outcome = (float(current) - float(initial)) / float(initial) * 100
                save_alert_outcome(row["id"], round(outcome, 4), minutes)

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
