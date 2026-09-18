"""In-process metrics + admin diagnostics."""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger("cx.monitoring")

_started_at = time.time()
_chain_errors: deque[dict[str, Any]] = deque(maxlen=50)
_api_errors: deque[dict[str, Any]] = deque(maxlen=50)
_counters: dict[str, int] = {
    "chain_errors": 0,
    "withdraw_auto_ok": 0,
    "withdraw_auto_fail": 0,
    "deposits_credited": 0,
    "binary_settled": 0,
    "swaps": 0,
}


def record_chain_error(source: str, detail: str) -> None:
    _counters["chain_errors"] += 1
    _chain_errors.appendleft({
        "ts": time.time(),
        "source": source,
        "detail": str(detail)[:500],
    })
    logger.warning("chain_error source=%s detail=%s", source, detail)


def record_api_error(path: str, detail: str) -> None:
    _api_errors.appendleft({
        "ts": time.time(),
        "path": path,
        "detail": str(detail)[:500],
    })


def bump(key: str, n: int = 1) -> None:
    _counters[key] = int(_counters.get(key, 0)) + n


def snapshot() -> dict[str, Any]:
    return {
        "uptime_sec": int(time.time() - _started_at),
        "counters": dict(_counters),
        "recent_chain_errors": list(_chain_errors)[:10],
        "recent_api_errors": list(_api_errors)[:10],
    }
