"""
Shared security helpers for handlers and API.
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict
from typing import Optional

from bot.config import settings

logger = logging.getLogger("cx.security")

# simple in-memory rate limit: key -> [timestamps]
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def sanitize_amount(value, *, min_v: float = 0.01, max_v: float = 1_000_000.0) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_amount") from exc
    if amount != amount:  # NaN
        raise ValueError("invalid_amount")
    if amount < min_v or amount > max_v:
        raise ValueError("amount_out_of_range")
    # 8 decimal places max (TON scale)
    return round(amount, 8)


def require_not_frozen(user_id: int) -> None:
    if settings.emergency_freeze and int(user_id) != int(settings.admin_id):
        raise PermissionError("emergency_freeze")
    if settings.maintenance_mode and int(user_id) != int(settings.admin_id):
        raise PermissionError("maintenance_mode")


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(settings.admin_id)


def rate_limit(key: str, *, limit: int = 20, window_sec: float = 60.0) -> bool:
    """
    Return True if allowed, False if limited.
    """
    now = time.time()
    bucket = _rate_buckets[key]
    _rate_buckets[key] = [t for t in bucket if now - t < window_sec]
    if len(_rate_buckets[key]) >= limit:
        return False
    _rate_buckets[key].append(now)
    return True


def daily_bonus_amount(user_id: int, day_key: str) -> float:
    """
    Deterministic small bonus in [0.20, 1.00] — not attacker-controllable RNG abuse.
    """
    h = hashlib.sha256(f"{user_id}:{day_key}:cx_bonus".encode()).hexdigest()
    n = int(h[:8], 16)
    # 0.20 .. 1.00 step 0.01
    return round(0.20 + (n % 81) / 100.0, 2)


def map_predict_symbol(asset: str) -> str:
    a = (asset or "").upper()
    mapping = {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
        "TON": "TONUSDT",
        "GOLD": "XAUUSDT",  # may fail on some exchanges
    }
    return mapping.get(a, "BTCUSDT")
