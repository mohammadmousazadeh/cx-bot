"""
Binary options MVP — open trade (debit ledger) + settle helpers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from bot.db.connection import get_db
from bot.db.ledger import (
    BusinessRuleError,
    InsufficientBalance,
    InvalidAmount,
    TxKind,
    credit_ton,
    debit_ton,
)
from bot.services.market import get_market_price

logger = logging.getLogger(__name__)

# MVP config
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_PAYOUT_RATE = 1.80
ALLOWED_DURATIONS = {15, 30, 60, 120, 300, 900}
MIN_AMOUNT = 1.0
MAX_AMOUNT = 50.0
ALLOWED_DIRECTIONS = {"up", "down"}


@dataclass
class BinaryConfig:
    symbol: str = DEFAULT_SYMBOL
    payout_rate: float = DEFAULT_PAYOUT_RATE
    min_amount: float = MIN_AMOUNT
    max_amount: float = MAX_AMOUNT
    durations_sec: tuple[int, ...] = (15, 30, 60, 120, 300, 900)


def get_config() -> dict[str, Any]:
    cfg = BinaryConfig()
    return {
        "symbol": cfg.symbol,
        "payout_rate": cfg.payout_rate,
        "min_amount": cfg.min_amount,
        "max_amount": cfg.max_amount,
        "durations_sec": list(cfg.durations_sec),
        "directions": sorted(ALLOWED_DIRECTIONS),
    }


async def open_binary_trade(
    user_id: int,
    *,
    direction: str,
    amount: float,
    duration_sec: int = 60,
    symbol: str = DEFAULT_SYMBOL,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """
    Open a binary trade:
    1) validate
    2) fetch entry price
    3) debit amount from ledger (binary_bet)
    4) insert binary_trades row status=open
    """
    direction = (direction or "").strip().lower()
    symbol = (symbol or DEFAULT_SYMBOL).strip().upper()

    if direction not in ALLOWED_DIRECTIONS:
        raise BusinessRuleError("invalid_direction")
    try:
        amount = float(amount)
    except (TypeError, ValueError) as exc:
        raise InvalidAmount("invalid_amount") from exc
    if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
        raise InvalidAmount(f"amount must be between {MIN_AMOUNT} and {MAX_AMOUNT}")
    if int(duration_sec) not in ALLOWED_DURATIONS:
        raise BusinessRuleError("invalid_duration")

    duration_sec = int(duration_sec)

    # Idempotency by client_request_id
    if client_request_id:
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT id, status, amount, direction, entry_price, expire_time
                FROM binary_trades
                WHERE user_id = ? AND client_request_id = ?
                """,
                (user_id, client_request_id),
            )
            existing = await cur.fetchone()
            if existing:
                return {
                    "ok": True,
                    "duplicate": True,
                    "trade": dict(existing),
                }

    entry_price = await get_market_price(symbol)
    if entry_price is None or entry_price <= 0:
        raise BusinessRuleError("price_unavailable")

    now = datetime.utcnow()
    expire = now + timedelta(seconds=duration_sec)

    # Debit first (fails if insufficient)
    ledger = await debit_ton(
        user_id,
        amount,
        kind=TxKind.BINARY_BET,
        meta={
            "symbol": symbol,
            "direction": direction,
            "duration_sec": duration_sec,
            "entry_price": entry_price,
        },
        idempotency_key=(
            f"binary_open:{user_id}:{client_request_id}"
            if client_request_id
            else None
        ),
    )

    async with get_db() as db:
        cur = await db.execute(
            """
            INSERT INTO binary_trades (
                user_id, symbol, direction, amount, payout_rate,
                entry_price, duration_sec, open_time, expire_time,
                status, profit, client_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, ?)
            """,
            (
                user_id,
                symbol,
                direction,
                amount,
                DEFAULT_PAYOUT_RATE,
                entry_price,
                duration_sec,
                now.isoformat(),
                expire.isoformat(),
                client_request_id,
            ),
        )
        await db.commit()
        trade_id = cur.lastrowid

    return {
        "ok": True,
        "trade": {
            "id": trade_id,
            "user_id": user_id,
            "symbol": symbol,
            "direction": direction,
            "amount": amount,
            "payout_rate": DEFAULT_PAYOUT_RATE,
            "potential_payout": round(amount * DEFAULT_PAYOUT_RATE, 8),
            "entry_price": entry_price,
            "duration_sec": duration_sec,
            "open_time": now.isoformat(),
            "expire_time": expire.isoformat(),
            "status": "open",
        },
        "balance_ton": ledger.ton_balance,
    }


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except Exception:
        return datetime.utcnow()


async def settle_trade(trade_id: int, *, exit_price: float | None = None) -> dict[str, Any]:
    """Settle one open trade by id."""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT * FROM binary_trades WHERE id = ?", (trade_id,)
        )
        row = await cur.fetchone()
        if not row:
            await db.execute("ROLLBACK")
            raise BusinessRuleError("trade_not_found")
        if row["status"] != "open":
            await db.execute("ROLLBACK")
            return {"ok": True, "already_settled": True, "trade": dict(row)}

        trade = dict(row)
        await db.commit()

    symbol = trade["symbol"]
    if exit_price is None:
        exit_price = await get_market_price(symbol)
        if exit_price is None or exit_price <= 0:
            exit_price = float(trade["entry_price"])

    entry = float(trade["entry_price"])
    direction = trade["direction"]
    amount = float(trade["amount"])
    rate = float(trade["payout_rate"] or DEFAULT_PAYOUT_RATE)

    went_up = exit_price >= entry
    won = (direction == "up" and went_up) or (direction == "down" and not went_up)

    profit = 0.0
    status = "lost"
    if won:
        payout = round(amount * rate, 8)
        profit = round(payout - amount, 8)
        status = "won"
        await credit_ton(
            int(trade["user_id"]),
            payout,
            kind=TxKind.BINARY_WIN,
            meta={
                "trade_id": trade_id,
                "entry_price": entry,
                "exit_price": exit_price,
                "direction": direction,
            },
            idempotency_key=f"binary_settle:{trade_id}",
        )

    async with get_db() as db:
        await db.execute(
            """
            UPDATE binary_trades
            SET status = ?, exit_price = ?, profit = ?
            WHERE id = ? AND status = 'open'
            """,
            (status, exit_price, profit if won else -amount, trade_id),
        )
        await db.commit()

    return {
        "ok": True,
        "trade_id": trade_id,
        "status": status,
        "entry_price": entry,
        "exit_price": exit_price,
        "profit": profit if won else -amount,
        "won": won,
    }


async def settle_due_trades(limit: int = 50) -> list[dict[str, Any]]:
    """Settle all open trades past expire_time."""
    now = datetime.utcnow().isoformat()
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT id FROM binary_trades
            WHERE status = 'open' AND expire_time <= ?
            ORDER BY expire_time ASC
            LIMIT ?
            """,
            (now, limit),
        )
        ids = [int(r["id"]) for r in await cur.fetchall()]

    results = []
    for tid in ids:
        try:
            results.append(await settle_trade(tid))
        except Exception as exc:
            logger.exception("settle trade %s failed", tid)
            results.append({"ok": False, "trade_id": tid, "error": str(exc)})
    return results


async def list_user_trades(
    user_id: int,
    *,
    limit: int = 20,
    only_open: bool = False,
) -> list[dict[str, Any]]:
    async with get_db() as db:
        if only_open:
            cur = await db.execute(
                """
                SELECT * FROM binary_trades
                WHERE user_id = ? AND status = 'open'
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            )
        else:
            cur = await db.execute(
                """
                SELECT * FROM binary_trades
                WHERE user_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            )
        return [dict(r) for r in await cur.fetchall()]
