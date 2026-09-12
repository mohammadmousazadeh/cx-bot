"""Sniper tick-prediction game backed by Ledger."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

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

logger = logging.getLogger("cx.sniper")

MIN_AMOUNT = 0.5
MAX_AMOUNT = 25.0
DEFAULT_PAYOUT = 1.85  # win returns stake * rate
DEFAULT_SYMBOL = "BTCUSDT"
ALLOWED = {"up", "down"}


def get_config() -> dict[str, Any]:
    return {
        "symbol": DEFAULT_SYMBOL,
        "min_amount": MIN_AMOUNT,
        "max_amount": MAX_AMOUNT,
        "payout_rate": DEFAULT_PAYOUT,
        "tick_window_sec": 3,
        "directions": sorted(ALLOWED),
    }


async def open_sniper_round(
    user_id: int,
    *,
    direction: str,
    amount: float,
    symbol: str = DEFAULT_SYMBOL,
) -> dict[str, Any]:
    direction = (direction or "").strip().lower()
    if direction not in ALLOWED:
        raise BusinessRuleError("invalid_direction")
    try:
        amount = float(amount)
    except (TypeError, ValueError) as exc:
        raise InvalidAmount("invalid_amount") from exc
    if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
        raise InvalidAmount(f"amount must be between {MIN_AMOUNT} and {MAX_AMOUNT}")

    # reject if already has open round
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id FROM sniper_rounds WHERE user_id = ? AND status = 'open' LIMIT 1",
            (user_id,),
        )
        if await cur.fetchone():
            raise BusinessRuleError("round_already_open")

    price = await get_market_price(symbol)
    if not price or price <= 0:
        raise BusinessRuleError("price_unavailable")

    now = datetime.utcnow()
    ledger = await debit_ton(
        user_id,
        amount,
        kind=TxKind.SNIPER_BET,
        meta={"direction": direction, "symbol": symbol, "entry_price": price},
    )

    async with get_db() as db:
        cur = await db.execute(
            """
            INSERT INTO sniper_rounds (
                user_id, symbol, direction, amount, payout_rate,
                entry_price, open_time, status, profit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0)
            """,
            (
                user_id,
                symbol.upper(),
                direction,
                amount,
                DEFAULT_PAYOUT,
                price,
                now.isoformat(),
            ),
        )
        await db.commit()
        rid = cur.lastrowid

    return {
        "ok": True,
        "round": {
            "id": rid,
            "symbol": symbol.upper(),
            "direction": direction,
            "amount": amount,
            "payout_rate": DEFAULT_PAYOUT,
            "entry_price": price,
            "open_time": now.isoformat(),
            "status": "open",
            "tick_window_sec": 3,
        },
        "balance_ton": ledger.ton_balance,
    }


async def settle_sniper_round(round_id: int, *, exit_price: float | None = None) -> dict[str, Any]:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM sniper_rounds WHERE id = ?", (round_id,))
        row = await cur.fetchone()
        if not row:
            raise BusinessRuleError("round_not_found")
        if row["status"] != "open":
            return {"ok": True, "already_settled": True, "round": dict(row)}
        data = dict(row)

    symbol = data["symbol"]
    if exit_price is None:
        exit_price = await get_market_price(symbol)
        if not exit_price or exit_price <= 0:
            exit_price = float(data["entry_price"])

    entry = float(data["entry_price"])
    direction = data["direction"]
    amount = float(data["amount"])
    rate = float(data["payout_rate"] or DEFAULT_PAYOUT)
    went_up = exit_price >= entry
    won = (direction == "up" and went_up) or (direction == "down" and not went_up)

    profit = 0.0
    status = "lost"
    if won:
        payout = round(amount * rate, 8)
        profit = round(payout - amount, 8)
        status = "won"
        await credit_ton(
            int(data["user_id"]),
            payout,
            kind=TxKind.SNIPER_WIN,
            meta={
                "round_id": round_id,
                "entry_price": entry,
                "exit_price": exit_price,
                "direction": direction,
            },
            idempotency_key=f"sniper_settle:{round_id}",
        )
    else:
        profit = -amount

    now = datetime.utcnow().isoformat()
    async with get_db() as db:
        await db.execute(
            """
            UPDATE sniper_rounds
            SET status = ?, exit_price = ?, profit = ?, settle_time = ?
            WHERE id = ? AND status = 'open'
            """,
            (status, exit_price, profit, now, round_id),
        )
        await db.commit()

    return {
        "ok": True,
        "round_id": round_id,
        "status": status,
        "won": won,
        "entry_price": entry,
        "exit_price": exit_price,
        "profit": profit,
        "amount": amount,
    }


async def list_sniper_rounds(user_id: int, *, limit: int = 20) -> list[dict]:
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT id, symbol, direction, amount, payout_rate, entry_price, exit_price,
                   status, profit, open_time, settle_time
            FROM sniper_rounds WHERE user_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]
