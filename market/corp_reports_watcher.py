"""
Монитор корпоративных отчётностей (MOEX ISS).

Архитектура:
  - updated_after: устанавливается по времени последней успешной проверки
  - publish_time freshness: игнорируем отчёты старше max_report_age минут
  - Приоритет: IMOEX / высокий free-float → более низкий порог частоты проверки
  - Дедупликация: id отчётов, виденных в текущей сессии
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Coroutine, Optional, Set

import aiohttp

logger = logging.getLogger(__name__)

# ─── MOEX ISS endpoint ──────────────────────────────────────────────────────────────

CORP_REPORTS_URL = (
    "https://iss.moex.com/iss/sitenews.json"
    "?rubric=12"            # корпоративные события
    "&limit=50"
    "&lang=ru"
)

# Fallback для событий по конкретному эмитенту
ISSUER_EVENTS_URL = (
    "https://iss.moex.com/iss/statistics/engines/stock/markets/shares/"
    "securities/{ticker}/events.json?limit=20&lang=ru"
)

# ─── Настройки ───────────────────────────────────────────────────────────────────

# Максимальный возраст отчёта (мин): события старше этого — игнорируем
MAX_REPORT_AGE_MINUTES = 15

# Период опроса для приоритетных тикеров (IMOEX / high free-float), сек
PRIORITY_POLL_INTERVAL = 60

# Период опроса для остальных тикеров
NORMAL_POLL_INTERVAL = 300

# Тикеры IMOEX (индекс Московской биржи) + крупнейшие по free-float
IMOEX_TICKERS: Set[str] = {
    "SBER", "GAZP", "LKOH", "GMKN", "NVTK", "TATN", "ROSN", "MGNT",
    "SNGS", "CHMF", "NLMK", "MTSS", "ALRS", "IRAO", "POLY", "PLZL",
    "PHOR", "MOEX", "AFLT", "RUAL", "MAGN", "VTBR", "CBOM", "BSPB",
    "FIXP", "YNDX", "OZON", "VKCO", "SBERP", "TRNFP",
}


@dataclass
class CorpReportAlert:
    ticker: str
    short_name: str
    event_type: str         # "финансовый отчёт" | "дивиденды" | "ГОСА" | …
    headline: str
    description: str
    publish_time: datetime
    source_url: str = ""
    is_priority: bool = False
    alert_type: str = "corp_report"

    def format_message(self) -> str:
        priority_tag = "🔑 Приоритет (IMOEX)\n" if self.is_priority else ""
        time_str = self.publish_time.strftime("%d.%m.%Y %H:%M")
        return (
            f"📋 <b>{self.ticker}</b> — {self.short_name}\n"
            f"🕐 {time_str}\n"
            f"{priority_tag}"
            f"\n"
            f"📰 <b>{self.event_type}</b>\n"
            f"{self.headline}\n"
            f"\n"
            f"{self.description[:300]}{'\u2026' if len(self.description) > 300 else ''}"
            + (f"\n\n🔗 <a href='{self.source_url}'>Источник</a>" if self.source_url else "")
        )


# ─── CorpReportsWatcher ──────────────────────────────────────────────────────────────

class CorpReportsWatcher:
    """
    Асинхронный монитор корпоративных отчётностей.
    Поддерживает freshness-фильтр и приоритет для IMOEX.
    """

    def __init__(
        self,
        tickers: list[tuple[str, str]],    # [(ticker, short_name), ...]
        alert_callback: Callable[..., Coroutine],
        max_report_age_minutes: int = MAX_REPORT_AGE_MINUTES,
        priority_poll_interval: float = PRIORITY_POLL_INTERVAL,
        normal_poll_interval: float = NORMAL_POLL_INTERVAL,
        imoex_tickers: Optional[Set[str]] = None,
    ):
        self._tickers = {ticker: name for ticker, name in tickers}
        self._callback = alert_callback
        self._max_age = timedelta(minutes=max_report_age_minutes)
        self._priority_interval = priority_poll_interval
        self._normal_interval = normal_poll_interval
        self._imoex = imoex_tickers if imoex_tickers is not None else IMOEX_TICKERS

        # Дедупликация по id новости (не сбрасывается между циклами)
        self._seen_ids: set[str] = set()

        # Время последней успешной проверки (UTC) — основа для updated_after
        self._last_check_utc: Optional[datetime] = None

        self._running: bool = False
        self._task: asyncio.Task | None = None

    # ── Опрос MOEX ISS ──────────────────────────────────────────────────────────────────

    async def _fetch_news(
        self,
        session: aiohttp.ClientSession,
        updated_after: Optional[datetime] = None,
    ) -> list[dict]:
        url = CORP_REPORTS_URL
        if updated_after:
            ts = updated_after.strftime("%Y-%m-%d %H:%M:%S")
            url += f"&updated_after={ts}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                raw = await resp.json(content_type=None)
                items = raw.get("sitenews", {})
                columns = items.get("columns", [])
                data = items.get("data", [])
                return [dict(zip(columns, row)) for row in data]
        except Exception as exc:
            logger.warning("corp_reports_watcher: fetch failed: %s", exc)
            return []

    # ── Разбор и фильтрация ─────────────────────────────────────────────────────────

    def _parse_publish_time(self, item: dict) -> Optional[datetime]:
        """Парсит publish_time/published_at из записи ISS."""
        for field_name in ("publish_date_time", "pubdate", "PUBLISH_DATE_TIME"):
            raw = item.get(field_name)
            if raw:
                try:
                    dt = datetime.fromisoformat(str(raw).replace(" ", "T"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except (ValueError, TypeError):
                    pass
        return None

    def _is_fresh(self, publish_time: Optional[datetime]) -> bool:
        """
        Шаг Freshness: отчёт не должен быть старше max_report_age.
        Используем event_time (publish_time), а не processing_time.
        """
        if publish_time is None:
            return False
        now_utc = datetime.now(timezone.utc)
        return (now_utc - publish_time) <= self._max_age

    def _extract_ticker(self, item: dict) -> Optional[str]:
        """Извлекает тикер из текста заголовка или поля security."""
        # Прямое поле
        for f in ("security_name", "SECURITY", "secid"):
            val = item.get(f, "")
            if val and val.upper() in self._tickers:
                return val.upper()

        # Поиск тикера в заголовке
        title = str(item.get("title", "") or item.get("TITLE", ""))
        for ticker in self._tickers:
            if ticker in title.upper():
                return ticker
        return None

    def _classify_event(self, item: dict) -> str:
        title = str(item.get("title", "") or "").lower()
        if any(w in title for w in ("дивид", "выплат")):
            return "Дивиденды"
        if any(w in title for w in ("отчёт", "отчет", "мсфо", "рсбу", "финанс")):
            return "Финансовый отчёт"
        if any(w in title for w in ("госа", "воса", "собрание акцион")):
            return "Собрание акционеров"
        if any(w in title for w in ("buyback", "байбэк", "выкуп акций")):
            return "Байбэк"
        return "Корпоративное событие"

    # ── Основной цикл ───────────────────────────────────────────────────────────────────

    async def _one_pass(self, session: aiohttp.ClientSession) -> None:
        now_utc = datetime.now(timezone.utc)

        # updated_after = время последней успешной проверки
        updated_after = self._last_check_utc

        items = await self._fetch_news(session, updated_after)
        if not items:
            return

        # Обновляем время проверки только при успешном ответе
        self._last_check_utc = now_utc

        for item in items:
            item_id = str(item.get("id", "") or item.get("ID", ""))
            if item_id in self._seen_ids:
                continue

            publish_time = self._parse_publish_time(item)

            # Freshness-фильтр: игнорируем старые события
            if not self._is_fresh(publish_time):
                # Всё равно добавляем в seen, чтобы не обрабатывать повторно
                if item_id:
                    self._seen_ids.add(item_id)
                continue

            ticker = self._extract_ticker(item)
            if ticker is None:
                if item_id:
                    self._seen_ids.add(item_id)
                continue

            if item_id:
                self._seen_ids.add(item_id)

            short_name = self._tickers.get(ticker, ticker)
            is_priority = ticker in self._imoex
            event_type = self._classify_event(item)

            title = str(item.get("title", "") or item.get("TITLE", ""))
            body = str(item.get("body_short", "") or item.get("BODY_SHORT", "")
                       or item.get("body", "") or "")
            url = str(item.get("url", "") or item.get("URL", ""))

            alert = CorpReportAlert(
                ticker=ticker,
                short_name=short_name,
                event_type=event_type,
                headline=title,
                description=body,
                publish_time=publish_time,
                source_url=url,
                is_priority=is_priority,
            )
            await self._callback(alert)

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            # Первый запуск: устанавливаем окно на max_report_age назад
            self._last_check_utc = datetime.now(timezone.utc) - self._max_age

            while self._running:
                try:
                    await self._one_pass(session)
                except Exception as exc:
                    logger.error("corp_reports_watcher: unexpected error: %s", exc)

                # Приоритетный режим: если хотя бы один IMOEX тикер отслеживается
                has_priority = any(t in self._imoex for t in self._tickers)
                interval = (
                    self._priority_interval if has_priority else self._normal_interval
                )
                await asyncio.sleep(interval)

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
