"""Market price helpers."""
from __future__ import annotations

from typing import Optional

import aiohttp


async def get_market_price(symbol: str = "BTCUSDT") -> Optional[float]:
    """Fetch last price from KuCoin, fallback Binance."""
    urls = [
        (
            f"https://api.kucoin.com/api/v1/market/orderbook/level1"
            f"?symbol={symbol.replace('USDT', '-USDT')}",
            "kucoin",
        ),
        (
            f"https://api1.binance.com/api/v3/ticker/price?symbol={symbol}",
            "binance",
        ),
    ]
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for url, source in urls:
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    if source == "kucoin":
                        return float(data["data"]["price"])
                    return float(data["price"])
            except Exception:
                continue
    return None


async def get_klines(symbol: str = "BTCUSDT", interval: str = "1m", limit: int = 100) -> list[dict]:
    """Return OHLCV candles from Binance public API."""
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
    out = []
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
