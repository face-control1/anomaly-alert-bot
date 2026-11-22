"""
Получение рыночных данных с Московской биржи через открытый MOEX ISS API.
Документация: https://iss.moex.com/iss/reference/
"""
import httpx
import logging
from typing import Any

logger = logging.getLogger(__name__)

BASE = "https://iss.moex.com/iss"


async def fetch_json(url: str, params: dict | None = None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.warning("MOEX API error %s: %s", url, exc)
        return None


async def get_moex_securities(board: str = "TQBR") -> list[dict]:
    """Возвращает список инструментов с текущими котировками."""
    url = f"{BASE}/engines/stock/markets/shares/boards/{board}/securities.json"
    data = await fetch_json(url, {"iss.meta": "off", "iss.only": "securities,marketdata"})
    if not data:
        return []

    sec_cols = data["securities"]["columns"]
    sec_data = data["securities"]["data"]
    md_cols = data["marketdata"]["columns"]
    md_data = data["marketdata"]["data"]

    securities: dict[str, dict] = {}
    for row in sec_data:
        d = dict(zip(sec_cols, row))
        securities[d["SECID"]] = {
            "ticker": d["SECID"],
            "shortname": d.get("SHORTNAME", ""),
            "exchange": "MOEX",
        }

    for row in md_data:
        d = dict(zip(md_cols, row))
        sec = securities.get(d["SECID"])
        if sec:
            sec["last"] = d.get("LAST") or d.get("MARKETPRICE")
            sec["open"] = d.get("OPEN")
            sec["low"] = d.get("LOW")
            sec["high"] = d.get("HIGH")
            sec["volume"] = d.get("VOLTODAY") or 0
            sec["trades"] = d.get("NUMTRADES") or 0

    return [v for v in securities.values() if v.get("last")]


async def get_moex_candles(
    ticker: str,
    board: str = "TQBR",
    interval: int = 1,
    n_bars: int = 30,
) -> list[dict]:
    """
    Последние n_bars минутных свечей для ticker на MOEX.
    interval: 1=1мин, 10=10мин, 60=1ч, 24=1день
    """
    url = (
        f"{BASE}/engines/stock/markets/shares/boards/{board}"
        f"/securities/{ticker}/candles.json"
    )
    data = await fetch_json(url, {
        "interval": interval,
        "iss.meta": "off",
        "iss.reverse": "true",
        "limit": n_bars,
    })
    if not data:
        return []
    cols = data["candles"]["columns"]
    return [dict(zip(cols, row)) for row in data["candles"]["data"]]


async def get_moex_ticker_info(ticker: str) -> dict | None:
    """Информация по конкретному тикеру."""
    url = f"{BASE}/securities/{ticker}.json"
    data = await fetch_json(url, {"iss.meta": "off"})
    if not data:
        return None
    cols = data["description"]["columns"]
    rows = data["description"]["data"]
    info: dict[str, Any] = {}
    for row in rows:
        d = dict(zip(cols, row))
        info[d["name"]] = d["value"]
    return info or None


async def search_moex_ticker(query: str) -> list[dict]:
    """Поиск тикера по строке."""
    url = f"{BASE}/securities.json"
    data = await fetch_json(url, {
        "q": query,
        "iss.meta": "off",
        "group_by": "type",
        "limit": 10,
    })
    if not data:
        return []
    cols = data["securities"]["columns"]
    return [dict(zip(cols, row)) for row in data["securities"]["data"]]
