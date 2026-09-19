"""Admin TOTP (RFC 6238) — external authenticator apps.

Set ADMIN_TOTP_SECRET to a base32 secret (e.g. from `python -c` generator).
When empty, 2FA is disabled (legacy behavior).
Sensitive admin actions require header/body field `admin_totp` or Telegram message code.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import struct
import time
from typing import Optional

from bot.config import settings

logger = logging.getLogger("cx.admin_2fa")


def _normalize_secret(secret: str) -> bytes:
    s = (secret or "").strip().replace(" ", "").upper()
    # pad base32
    pad = (-len(s)) % 8
    s = s + ("=" * pad)
    try:
        return base64.b32decode(s, casefold=True)
    except Exception:
        # fallback: use raw utf-8 as key material
        return hashlib.sha1(secret.encode("utf-8")).digest()


def totp_code(secret: str, for_time: Optional[float] = None, step: int = 30, digits: int = 6) -> str:
    if not secret:
        return ""
    t = int((for_time if for_time is not None else time.time()) // step)
    key = _normalize_secret(secret)
    msg = struct.pack(">Q", t)
    dig = hmac.new(key, msg, hashlib.sha1).digest()
    off = dig[-1] & 0x0F
    num = struct.unpack(">I", dig[off:off + 4])[0] & 0x7FFFFFFF
    return str(num % (10 ** digits)).zfill(digits)


def verify_totp(code: str, secret: Optional[str] = None, window: int = 1) -> bool:
    secret = secret if secret is not None else getattr(settings, "admin_totp_secret", "") or ""
    if not secret:
        return True  # disabled
    c = str(code or "").strip().replace(" ", "")
    if not c.isdigit():
        return False
    now = time.time()
    for w in range(-window, window + 1):
        if hmac.compare_digest(c, totp_code(secret, for_time=now + w * 30)):
            return True
    return False


def is_2fa_enabled() -> bool:
    return bool((getattr(settings, "admin_totp_secret", "") or "").strip())


def generate_secret() -> str:
    import secrets
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode("ascii").rstrip("=")
