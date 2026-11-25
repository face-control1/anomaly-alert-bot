"""
MOEX ISS API — получение рыночных данных.
"""
from __future__ import annotations

import logging
import asyncio
import ssl
import os
from typing import Optional

import certifi
import httpx

logger = logging.getLogger(__name__)

# Публичный ISS официально поддерживает HTTP. Это обходит повреждённую TLS-цепочку
# MOEX в некоторых регионах Render; для приватных данных этот канал не используется.
BASE_URL = os.getenv("MOEX_ISS_BASE", "http://iss.moex.com/iss").rstrip("/")
_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            verify=ssl_context,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"User-Agent": "MoexHelpBot/1.0"},
        )
    return _client


async def fetch_json(url: str, params: dict | None = None) -> dict | None:
    for attempt in range(1, 4):
        try:
            r = await get_client().get(url, params=params)
            r.raise_for_status()
            return r.json()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            if attempt == 3:
                logger.warning("MOEX request failed after %d attempts: %s (%s)", attempt, url, exc)
                return None
            await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
        except (ValueError, TypeError) as exc:
            logger.warning("MOEX returned invalid JSON: %s (%s)", url, exc)
            return None
    return None


async def get_all_moex_tickers(board: str = "TQBR") -> list[str]:
    """Fetches all actively traded tickers from MOEX TQBR board."""
    url = f"{BASE_URL}/engines/stock/markets/shares/boards/{board}/securities.json"
    data = await fetch_json(url, {"iss.meta": "off", "iss.only": "securities", "securities.columns": "SECID,STATUS,LOTSIZE"})
    if not data:
        return []
    rows = data.get("securities", {}).get("data", [])
    return [r[0] for r in rows if r[1] == "A" and r[2]]


async def get_moex_marketdata(tickers: list[str], board: str = "TQBR") -> dict[str, dict]:
    """Batch fetch market data for all tickers."""
    url = f"{BASE_URL}/engines/stock/markets/shares/boards/{board}/securities.json"
    data = await fetch_json(url, {"iss.meta": "off", "iss.only": "marketdata", "marketdata.columns": "SECID,LAST,CHANGE,VOLTODAY,VALTODAY,OPEN,HIGH,LOW"})
    if not data:
        return {}
    rows = data.get("marketdata", {}).get("data", [])
    cols = ["SECID", "LAST", "CHANGE", "VOLTODAY", "VALTODAY", "OPEN", "HIGH", "LOW"]
    result = {}
    for r in rows:
        d = dict(zip(cols, r))
        secid = d.pop("SECID")
        if secid in tickers:
            result[secid] = d
    return result


async def get_moex_securities_info(tickers: list[str], board: str = "TQBR") -> dict[str, dict]:
    """Batch fetch securities info (names, lot sizes, etc.)."""
    url = f"{BASE_URL}/engines/stock/markets/shares/boards/{board}/securities.json"
    data = await fetch_json(url, {"iss.meta": "off", "iss.only": "securities", "securities.columns": "SECID,SHORTNAME,LOTSIZE,FACEVALUE"})
    if not data:
        return {}
    rows = data.get("securities", {}).get("data", [])
    cols = ["SECID", "SHORTNAME", "LOTSIZE", "FACEVALUE"]
    result = {}
    for r in rows:
        d = dict(zip(cols, r))
        secid = d.pop("SECID")
        if secid in tickers:
            result[secid] = d
    return result


async def get_moex_candles(
    ticker: str,
    board: str = "TQBR",
    interval: int = 1,
    n_bars: int = 30,
) -> list[dict]:
    """Fetch recent candles for a ticker."""
    url = f"{BASE_URL}/engines/stock/markets/shares/boards/{board}/securities/{ticker}/candles.json"
    data = await fetch_json(url, {"interval": interval, "iss.meta": "off", "iss.reverse": "true", "limit": n_bars + 2})
    if not data:
        return []
    rows = data.get("candles", {}).get("data", [])
    cols = data.get("candles", {}).get("columns", ["open", "close", "high", "low", "value", "volume", "begin", "end"])
    return [dict(zip(cols, r)) for r in rows]


async def get_moex_candles_weekly(ticker: str, board: str = "TQBR") -> list[dict]:
    """Fetch weekly candles (interval=7) for weekly change calculation."""
    return await get_moex_candles(ticker, board, interval=7, n_bars=3)


async def search_moex_ticker(query: str) -> list[dict]:
    """Search for a ticker by name or SECID."""
    url = f"{BASE_URL}/securities.json"
    data = await fetch_json(url, {"q": query, "iss.meta": "off", "limit": 10, "group_by": "group", "group_by_filter": "stock_shares"})
    if not data:
        return []
    rows = data.get("securities", {}).get("data", [])
    cols = data.get("securities", {}).get("columns", [])
    return [dict(zip(cols, r)) for r in rows]


async def get_weekly_change(ticker: str, current_price: float, board: str = "TQBR") -> float | None:
    """Calculate weekly price change as percentage."""
    candles = await get_moex_candles_weekly(ticker, board)
    if len(candles) < 2:
        return None
    week_ago_close = candles[-1].get("close") or candles[-1].get("open")
    if not week_ago_close:
        return None
    return round((current_price - float(week_ago_close)) / float(week_ago_close) * 100, 2)
