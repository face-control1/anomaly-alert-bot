"""
Детектор корпоративных отчётов через MOEX CCI API.
Polling каждые N минут, приоритет — компании из индекса MOEX и с высоким free-float.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Awaitable, Optional
import aiohttp

logger = logging.getLogger(__name__)

# MOEX CCI API — корпоративные отчёты
CCI_REPORTS_URL = "https://iss.moex.com/iss/cci/reporting/corp-info/reports.json"

# Список тикеров из индекса IMOEX (обновить при необходимости)
IMOEX_TICKERS = {
    "SBER", "GAZP", "LKOH", "NVTK", "TATN", "ROSN", "SNGS", "CHMF",
    "ALRS", "MTSS", "MGNT", "NLMK", "MAGN", "PHOR", "YNDX", "TCSG",
    "VTBR", "AFKS", "MOEX", "RUAL", "GMKN", "PLZL", "POLY", "PIKK",
    "FIVE", "FIXP", "OZON", "VKCO", "AFLT", "HYDR", "FEES", "IRAO",
    "TRNFP", "BANE", "UWSB", "SELG", "LSRG", "ETLN", "SMLT", "DSKY",
}

# Типы отчётов (приоритетные)
PRIORITY_REPORT_TYPES = {
    "quarterly": "📊 Квартальный отчёт",
    "annual": "📋 Годовой отчёт",
    "semi_annual": "📈 Полугодовой отчёт",
    "current": "📌 Текущий отчёт",
}


@dataclass
class CorpReportAlert:
    ticker: str
    company_name: str
    report_type: str
    report_date: str
    description: str
    is_priority: bool  # из индекса IMOEX
    url: Optional[str] = None


@dataclass
class _ReportRecord:
    report_id: str
    ticker: str


class CorpReportsWatcher:
    def __init__(
        self,
        alert_callback: Callable[[CorpReportAlert], Awaitable[None]],
        interval_seconds: int = 120,  # каждые 2 минуты
        priority_only: bool = False,
    ):
        self.alert_callback = alert_callback
        self.interval_seconds = interval_seconds
        self.priority_only = priority_only
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._seen_ids: set[str] = set()
        self._initialized = False

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("CorpReportsWatcher started")

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
                await self._poll()
            except Exception as e:
                logger.error("CorpReportsWatcher error: %s", e)
            await asyncio.sleep(self.interval_seconds)

    async def _poll(self):
        # Запрашиваем отчёты за последние 24 часа
        updated_after = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        params = {
            "updated_after": updated_after,
            "iss.meta": "off",
            "limit": 100,
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    CCI_REPORTS_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("CCI API status: %d", resp.status)
                return
                    data = await resp.json(content_type=None)
            except Exception as e:
                logger.debug("CCI API fetch error: %s", e)
                return

        reports = self._parse_reports(data)

        if not self._initialized:
            # При первом запуске просто заполняем кеш, не шлём алерты
            for r in reports:
                self._seen_ids.add(r.report_id)
            self._initialized = True
            logger.info("CorpReportsWatcher initialized, %d reports cached", len(reports))
            return

        new_reports = [r for r in reports if r.report_id not in self._seen_ids]
        for report in new_reports:
            self._seen_ids.add(report.report_id)
            if self.priority_only and not report.is_priority:
                continue
            try:
                await self.alert_callback(report)
            except Exception as e:
                logger.error("CorpReport alert callback error: %s", e)

    def _parse_reports(self, data: dict) -> list[CorpReportAlert]:
        results = []
        try:
            # Пробуем оба возможных ключа ответа
            block = data.get("reports") or data.get("corp_reports") or {}
            columns = block.get("columns", [])
            rows = block.get("data", [])
            if not columns or not rows:
                return results

            col = {name: i for i, name in enumerate(columns)}

            for row in rows:
                report_id = str(row[col.get("id", 0)] or "")
                ticker = str(row[col.get("issuer_id", col.get("ticker", 1))] or "").upper().strip()
                company = str(row[col.get("full_name", col.get("issuer_name", 2))] or "")
                rtype = str(row[col.get("report_type", col.get("type", 3))] or "").lower()
                rdate = str(row[col.get("report_date", col.get("date", 4))] or "")
                desc = str(row[col.get("description", col.get("title", 5))] or "")
                url = str(row[col.get("url", col.get("link", 6))] or "") if len(row) > 6 else None

                if not report_id or not ticker:
                    continue

                # Человекочитаемый тип
                friendly_type = PRIORITY_REPORT_TYPES.get(rtype, f"📄 {rtype.capitalize()}")

                is_priority = ticker in IMOEX_TICKERS

                results.append(CorpReportAlert(
                    ticker=ticker,
                    company_name=company or ticker,
                    report_type=friendly_type,
                    report_date=rdate,
                    description=desc[:200] if desc else "—",
                    is_priority=is_priority,
                    url=url if url and url.startswith("http") else None,
                ))
        except Exception as e:
            logger.debug("CorpReport parse error: %s", e)

        return results
