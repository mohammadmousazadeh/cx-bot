"""Periodic SQLite backup worker."""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from bot.config import settings

logger = logging.getLogger("cx.worker.backup")
_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


async def _loop(interval: float) -> None:
    logger.info("DB backup worker started (interval=%ss)", interval)
    while not _stop.is_set():
        try:
            src = Path(settings.db_name)
            if src.exists():
                out_dir = Path(settings.backup_dir or "backups")
                out_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                dest = out_dir / f"cx_db_{stamp}.db"
                shutil.copy2(src, dest)
                # keep last 20
                files = sorted(out_dir.glob("cx_db_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in files[20:]:
                    try:
                        old.unlink()
                    except Exception:
                        pass
                logger.info("Backup written %s", dest)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("backup failed")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    logger.info("DB backup worker stopped")


def start_backup_worker() -> asyncio.Task:
    global _task
    if _task and not _task.done():
        return _task
    _stop.clear()
    interval = max(300.0, float(getattr(settings, "backup_interval_sec", 3600) or 3600))
    _task = asyncio.create_task(_loop(interval), name="db_backup")
    return _task


async def stop_backup_worker() -> None:
    global _task
    _stop.set()
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
