"""
Background worker: settle expired binary trades automatically.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot

from bot.config import settings
from bot.services.binary import settle_due_trades

logger = logging.getLogger("cx.worker.binary")

_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


async def _notify_results(bot: Bot | None, settled: list[dict]) -> None:
    if not bot:
        return
    for item in settled:
        if not item.get("ok") or item.get("already_settled"):
            continue
        trade_id = item.get("trade_id")
        # Load user_id from DB row if present in result — settle_trade doesn't include user_id
        # Skip notify if we can't resolve; optional enrichment below
        try:
            from bot.db.connection import get_db

            async with get_db() as db:
                cur = await db.execute(
                    "SELECT user_id, amount, direction, status, profit, symbol FROM binary_trades WHERE id = ?",
                    (trade_id,),
                )
                row = await cur.fetchone()
            if not row:
                continue
            uid = int(row["user_id"])
            status = row["status"]
            profit = float(row["profit"] or 0)
            amount = float(row["amount"] or 0)
            direction = row["direction"]
            symbol = row["symbol"]
            if status == "won":
                text = (
                    f" *باینری #{trade_id}*\n"
                    f"نتیجه: برد\n"
                    f"{symbol} | {direction}\n"
                    f"سود: `{profit:g} TON`"
                )
            else:
                text = (
                    f" *باینری #{trade_id}*\n"
                    f"نتیجه: باخت\n"
                    f"{symbol} | {direction}\n"
                    f"مبلغ: `{amount:g} TON`"
                )
            await bot.send_message(uid, text, parse_mode="Markdown")
            await asyncio.sleep(0.05)
        except Exception:
            logger.debug("notify failed for trade %s", trade_id, exc_info=True)


async def _loop(bot: Bot | None, interval: float) -> None:
    logger.info("Binary settler started (interval=%ss)", interval)
    while not _stop.is_set():
        try:
            results = await settle_due_trades(limit=100)
            if results:
                won = sum(1 for r in results if r.get("won"))
                lost = sum(1 for r in results if r.get("ok") and r.get("won") is False)
                logger.info(
                    "Settled %s trades (won=%s lost=%s)",
                    len(results),
                    won,
                    lost,
                )
                await _notify_results(bot, results)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("binary settler iteration failed")

        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    logger.info("Binary settler stopped")


def start_binary_settler(bot: Bot | None = None) -> asyncio.Task:
    """Start background task (idempotent)."""
    global _task
    if _task and not _task.done():
        return _task
    _stop.clear()
    interval = float(getattr(settings, "binary_settle_interval", 5) or 5)
    interval = max(2.0, interval)
    _task = asyncio.create_task(_loop(bot, interval), name="binary_settler")
    return _task


async def stop_binary_settler() -> None:
    global _task
    _stop.set()
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
