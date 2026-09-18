"""Periodic SQLite backup worker — safe online backup onto persistent volume."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from bot.config import settings

logger = logging.getLogger("cx.worker.backup")
_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


def _resolve_paths() -> tuple[Path, Path]:
    src = Path(settings.db_name)
    # Prefer backups next to DB on the same volume (Railway /app/data)
    configured = Path(settings.backup_dir or "backups")
    if configured.is_absolute():
        out_dir = configured
    else:
        # relative → under DB parent (e.g. /app/data/backups)
        out_dir = (src.parent / configured) if src.parent.as_posix() not in (".", "") else configured
    return src, out_dir


def _safe_backup_sync(src: Path, dest: Path) -> None:
    """Use SQLite online backup API (consistent while DB is in use)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Temporary file then rename for atomicity
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    src_conn = sqlite3.connect(str(src), timeout=60)
    try:
        # Ensure WAL-friendly checkpoint isn't required; backup API is consistent
        dst_conn = sqlite3.connect(str(tmp), timeout=60)
        try:
            src_conn.backup(dst_conn)
            dst_conn.commit()
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    tmp.replace(dest)


def _prune(out_dir: Path, keep: int) -> None:
    files = sorted(out_dir.glob("cx_db_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except Exception:
            logger.exception("failed to remove old backup %s", old)


async def run_backup_once(*, keep: int | None = None) -> dict:
    """Run one backup; returns metadata for API/admin."""
    src, out_dir = _resolve_paths()
    if not src.exists():
        return {"ok": False, "error": "db_missing", "path": str(src)}
    keep_n = keep if keep is not None else 20
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"cx_db_{stamp}.db"

    def _job() -> int:
        _safe_backup_sync(src, dest)
        _prune(out_dir, keep_n)
        return dest.stat().st_size

    size = await asyncio.to_thread(_job)
    logger.info("Backup written %s (%s bytes)", dest, size)
    return {
        "ok": True,
        "path": str(dest),
        "size": size,
        "created_at": stamp,
        "source": str(src),
    }


def list_backups(limit: int = 20) -> list[dict]:
    _, out_dir = _resolve_paths()
    if not out_dir.exists():
        return []
    files = sorted(out_dir.glob("cx_db_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        st = f.stat()
        out.append({
            "name": f.name,
            "path": str(f),
            "size": st.st_size,
            "mtime": datetime.utcfromtimestamp(st.st_mtime).isoformat() + "Z",
        })
    return out


async def _loop(interval: float) -> None:
    logger.info("DB backup worker started (interval=%ss)", interval)
    # Small delay so app finishes boot
    try:
        await asyncio.wait_for(_stop.wait(), timeout=15)
        return
    except asyncio.TimeoutError:
        pass
    while not _stop.is_set():
        try:
            await run_backup_once()
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
    if not getattr(settings, "backup_enabled", True):
        logger.info("DB backup worker disabled (BACKUP_ENABLED=false)")
        # dummy completed task not needed; return a no-op sleeping task
        async def _idle():
            await _stop.wait()
        _task = asyncio.create_task(_idle(), name="db_backup_idle")
        return _task
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
