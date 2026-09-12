"""Background worker: scan TON deposits and credit ledger."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot

from bot.config import settings
from bot.services.ton_chain import credit_new_deposits

logger = logging.getLogger("cx.worker.deposit")

_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


async def _notify(bot: Bot | None, credited: list[dict]) -> None:
    if not bot:
        return
    for row in credited:
        if not row.get("ok") or row.get("duplicate"):
            continue
        uid = row.get("user_id")
        amount = row.get("amount")
        tx_hash = str(row.get("tx_hash") or "")
        if not uid:
            continue
        try:
            tx_show = tx_hash if len(tx_hash) <= 28 else tx_hash[:28] + "..."
            await bot.send_message(
                int(uid),
                "Deposit confirmed\nAmount: `%s` TON\nTX: `%s`" % (amount, tx_show),
                parse_mode="Markdown",
            )
        except Exception:
            logger.debug("deposit notify failed user=%s", uid, exc_info=True)


async def _loop(bot: Bot | None, interval: float) -> None:
    logger.info("Deposit scanner started (interval=%ss)", interval)
    while not _stop.is_set():
        try:
            results = await credit_new_deposits()
            new_ones = [
                r
                for r in results
                if r.get("ok") and not r.get("duplicate") and r.get("amount")
            ]
            if new_ones:
                logger.info("Credited %s new deposits", len(new_ones))
                await _notify(bot, new_ones)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("deposit scanner iteration failed")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    logger.info("Deposit scanner stopped")


def start_deposit_scanner(bot: Bot | None = None) -> asyncio.Task:
    global _task
    if _task and not _task.done():
        return _task
    _stop.clear()
    interval = float(getattr(settings, "deposit_worker_interval", 30) or 30)
    interval = max(10.0, interval)
    _task = asyncio.create_task(_loop(bot, interval), name="deposit_scanner")
    return _task


async def stop_deposit_scanner() -> None:
    global _task
    _stop.set()
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
