"""Market price and kline helpers with multi-exchange fallback."""
from __future__ import annotations

import time
from typing import Any, Optional

import aiohttp


def _kucoin_symbol(symbol: str) -> str:
    s = (symbol or "BTCUSDT").upper().replace("-", "")
    if s.endswith("USDT"):
        return s[:-4] + "-USDT"
    return s


def _base_quote(symbol: str) -> tuple[str, str]:
    s = (symbol or "BTCUSDT").upper().replace("-", "").replace("_", "")
    if s.endswith("USDT"):
        return s[:-4], "USDT"
    return s, "USDT"


async def get_market_price(symbol: str = "BTCUSDT") -> Optional[float]:
    """Fetch last price with multiple public fallbacks."""
    symbol = (symbol or "BTCUSDT").upper().replace("-", "").replace("_", "")
    base, quote = _base_quote(symbol)
    timeout = aiohttp.ClientTimeout(total=7)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 1) KuCoin
        try:
            url = (
                f"https://api.kucoin.com/api/v1/market/orderbook/level1"
                f"?symbol={base}-{quote}"
            )
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float((data.get("data") or {}).get("price") or 0)
                    if price > 0:
                        return price
        except Exception:
            pass

        # 2) Binance
        try:
            url = f"https://api1.binance.com/api/v3/ticker/price?symbol={symbol}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data.get("price") or 0)
                    if price > 0:
                        return price
        except Exception:
            pass

        # 3) OKX
        try:
            url = f"https://www.okx.com/api/v5/market/ticker?instId={base}-{quote}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rows = data.get("data") or []
                    if rows:
                        price = float(rows[0].get("last") or 0)
                        if price > 0:
                            return price
        except Exception:
            pass

        # 4) Bybit
        try:
            url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rows = ((data.get("result") or {}).get("list")) or []
                    if rows:
                        price = float(rows[0].get("lastPrice") or 0)
                        if price > 0:
                            return price
        except Exception:
            pass

        # 5) CoinGecko simple (good for TON when exchanges block)
        gecko_ids = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "TON": "the-open-network",
        }
        gid = gecko_ids.get(base)
        if gid and quote == "USDT":
            try:
                url = (
                    f"https://api.coingecko.com/api/v3/simple/price"
                    f"?ids={gid}&vs_currencies=usd"
                )
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        price = float((data.get(gid) or {}).get("usd") or 0)
                        if price > 0:
                            return price
            except Exception:
                pass

    return None


async def _klines_binance(symbol: str, interval: str, limit: int) -> list[dict[str, Any]]:
    url = (
        f"https://api1.binance.com/api/v3/klines"
        f"?symbol={symbol.upper()}&interval={interval}&limit={int(limit)}"
    )
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return []
            raw = await resp.json()
    out: list[dict[str, Any]] = []
    for k in raw:
        out.append({
            "time": int(k[0]) // 1000,
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    return out


_KUCOIN_TYPE = {
    "1m": "1min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1hour",
    "4h": "4hour",
    "1d": "1day",
}


async def _klines_kucoin(symbol: str, interval: str, limit: int) -> list[dict[str, Any]]:
    ktype = _KUCOIN_TYPE.get(interval, "1min")
    end = int(time.time())
    sec = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}.get(interval, 60)
    start = end - sec * int(limit) - sec
    base, quote = _base_quote(symbol)
    url = (
        f"https://api.kucoin.com/api/v1/market/candles"
        f"?type={ktype}&symbol={base}-{quote}&startAt={start}&endAt={end}"
    )
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    rows = data.get("data") or []
    out: list[dict[str, Any]] = []
    for k in reversed(rows):
        try:
            out.append({
                "time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[3]),
                "low": float(k[4]),
                "close": float(k[2]),
                "volume": float(k[5]),
            })
        except Exception:
            continue
    return out[-int(limit):]


async def get_klines(symbol: str = "BTCUSDT", interval: str = "1m", limit: int = 100) -> list[dict]:
    """Return OHLCV candles — Binance first, KuCoin fallback."""
    limit = max(10, min(int(limit or 100), 500))
    interval = interval or "1m"
    for fetcher in (_klines_binance, _klines_kucoin):
        try:
            rows = await fetcher(symbol, interval, limit)
            if rows:
                return rows
        except Exception:
            continue
    return []
