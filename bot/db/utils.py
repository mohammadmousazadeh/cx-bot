"""Shared DB/validation utilities."""
from __future__ import annotations

import asyncio
import re
import socket

DISPOSABLE_DOMAINS = {
    "tempmail.com",
    "10minutemail.com",
    "guerrillamail.com",
    "mailinator.com",
    "throwawaymail.com",
    "yopmail.com",
    "trashmail.com",
    "sharklasers.com",
    "getairmail.com",
    "dispostable.com",
    "temp-mail.org",
    "fakeinbox.com",
}


async def is_disposable_email(email: str) -> bool:
    """Return True if email looks invalid or uses a disposable domain."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return True
    domain = email.split("@", 1)[1]
    if domain in DISPOSABLE_DOMAINS:
        return True
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, socket.gethostbyname, domain)
        return False
    except Exception:
        return True
