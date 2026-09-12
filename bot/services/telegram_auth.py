"""
Telegram WebApp initData validation (HMAC-SHA-256).

Spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Never trust client-side user_id, balance, or kyc without this check.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qsl


class InitDataError(Exception):
    """Raised when initData is missing, expired, or forged."""


@dataclass(frozen=True)
class WebAppUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = ""
    is_premium: bool = False


@dataclass(frozen=True)
class ValidatedInitData:
    user: WebAppUser
    auth_date: int
    query_id: Optional[str]
    start_param: Optional[str]
    raw: dict[str, str]


def _parse_user(raw: str) -> WebAppUser:
    data = json.loads(raw)
    return WebAppUser(
        id=int(data["id"]),
        first_name=str(data.get("first_name") or ""),
        last_name=str(data.get("last_name") or ""),
        username=str(data.get("username") or ""),
        language_code=str(data.get("language_code") or ""),
        is_premium=bool(data.get("is_premium") or False),
    )


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86400,
) -> ValidatedInitData:
    """
    Validate Telegram.WebApp.initData string.

    Args:
        init_data: Full initData query string from the Mini App
        bot_token: Bot token from BotFather
        max_age_seconds: Reject if auth_date older than this (default 24h)

    Raises:
        InitDataError on any validation failure
    """
    if not init_data or not isinstance(init_data, str):
        raise InitDataError("init_data is empty")
    if not bot_token:
        raise InitDataError("bot_token is empty")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("hash missing")

    # data_check_string: sorted key=value joined by newline (hash excluded)
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(pairs.items(), key=lambda x: x[0])
    )

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()

    calculated = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise InitDataError("invalid hash — data may be forged")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataError("invalid auth_date") from exc

    now = int(time.time())
    if auth_date <= 0:
        raise InitDataError("auth_date missing")
    if now - auth_date > max_age_seconds:
        raise InitDataError("init_data expired — reopen the Mini App")

    user_raw = pairs.get("user")
    if not user_raw:
        raise InitDataError("user missing in initData")

    try:
        user = _parse_user(user_raw)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InitDataError(f"invalid user payload: {exc}") from exc

    if user.id <= 0:
        raise InitDataError("invalid user id")

    return ValidatedInitData(
        user=user,
        auth_date=auth_date,
        query_id=pairs.get("query_id"),
        start_param=pairs.get("start_param"),
        raw=pairs,
    )


def extract_init_data_from_headers(headers: Any) -> str:
    """Read initData from common header names."""
    if headers is None:
        return ""
    # Mapping / CaseInsensitiveDict compatible
    for key in (
        "X-Telegram-Init-Data",
        "x-telegram-init-data",
        "Authorization",
    ):
        try:
            val = headers.get(key)
        except Exception:
            val = None
        if not val:
            continue
        val = str(val).strip()
        if val.lower().startswith("tma "):
            return val[4:].strip()
        if key.lower() != "authorization":
            return val
    return ""
