"""Signed short-lived tokens for Mini App auth when Telegram initData is unavailable."""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass


@dataclass
class TokenUser:
    id: int
    username: str = ""
    first_name: str = ""
    language_code: str = ""


@dataclass
class ValidatedToken:
    user: TokenUser
    auth_date: int


def make_webapp_token(user_id: int, bot_token: str, ttl_sec: int = 86400) -> tuple[int, str]:
    exp = int(time.time()) + max(60, ttl_sec)
    msg = f"{user_id}:{exp}".encode("utf-8")
    sig = hmac.new(bot_token.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:40]
    return exp, sig


def verify_webapp_token(
    user_id: int,
    exp: int,
    sig: str,
    bot_token: str,
) -> ValidatedToken:
    if user_id <= 0:
        raise ValueError("invalid user_id")
    now = int(time.time())
    if exp < now:
        raise ValueError("token expired")
    if exp > now + 7 * 86400:
        raise ValueError("token exp too far")
    msg = f"{user_id}:{exp}".encode("utf-8")
    expected = hmac.new(bot_token.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:40]
    if not hmac.compare_digest(expected, (sig or "").strip().lower()):
        if not hmac.compare_digest(expected, (sig or "").strip()):
            raise ValueError("invalid signature")
    return ValidatedToken(user=TokenUser(id=int(user_id)), auth_date=now)
