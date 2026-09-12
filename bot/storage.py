"""
FSM storage factory.

- Default: MemoryStorage (single process)
- Production multi-instance: set REDIS_URL → RedisStorage
"""
from __future__ import annotations

import logging
from typing import Any

from bot.config import settings

logger = logging.getLogger(__name__)


def create_fsm_storage() -> Any:
    redis_url = (settings.redis_url or "").strip()
    if not redis_url:
        from aiogram.fsm.storage.memory import MemoryStorage

        logger.info("FSM storage: MemoryStorage (single instance)")
        return MemoryStorage()

    try:
        from aiogram.fsm.storage.redis import RedisStorage
        from redis.asyncio import Redis
    except ImportError as exc:
        raise RuntimeError(
            "REDIS_URL is set but redis package is missing. "
            "Install with: pip install redis"
        ) from exc

    redis = Redis.from_url(redis_url, decode_responses=True)
    storage = RedisStorage(redis=redis)
    logger.info("FSM storage: RedisStorage (%s)", redis_url.split("@")[-1])
    return storage
