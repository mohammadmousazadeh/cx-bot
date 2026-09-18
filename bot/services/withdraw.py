"""Withdraw helpers: auto on-chain settlement under threshold."""
from __future__ import annotations

import logging
from typing import Any

from bot.config import settings
from bot.db.ledger import complete_withdraw, reject_withdraw

logger = logging.getLogger("cx.withdraw")


async def try_auto_withdraw(request_id: int, amount: float, address: str) -> dict[str, Any]:
    """
    If enabled and amount <= AUTO_WITHDRAW_MAX and hot wallet ready,
    send on-chain and complete the request.
    """
    if not getattr(settings, "auto_withdraw_enabled", True):
        return {"auto": False, "reason": "disabled"}
    if not getattr(settings, "withdraw_onchain_enabled", True):
        return {"auto": False, "reason": "onchain_disabled"}
    max_amt = float(getattr(settings, "auto_withdraw_max", 20) or 20)
    if float(amount) > max_amt:
        return {"auto": False, "reason": "above_limit", "max": max_amt}

    from bot.services.ton_chain import (
        TonSendError,
        TonWalletNotConfigured,
        hot_wallet_configured,
        send_ton,
    )

    if not hot_wallet_configured():
        return {"auto": False, "reason": "no_hot_wallet"}

    try:
        tx_hash = await send_ton(str(address), float(amount), comment=f"cx_wd_{request_id}")
        await complete_withdraw(request_id, tx_hash=tx_hash)
        logger.info("Auto-withdraw completed req=%s amount=%s tx=%s", request_id, amount, tx_hash)
        return {"auto": True, "tx_hash": tx_hash}
    except (TonWalletNotConfigured, TonSendError) as exc:
        logger.warning("Auto-withdraw failed req=%s: %s", request_id, exc)
        return {"auto": False, "reason": "send_failed", "error": str(exc)}


async def settle_withdraw_onchain(request_id: int) -> dict[str, Any]:
    """Load pending withdraw, send TON on-chain if enabled, complete ledger."""
    import aiosqlite

    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT request_id, user_id, amount, address, status FROM requests WHERE request_id = ?",
            (request_id,),
        )
        req = await cur.fetchone()
    if not req:
        return {"ok": False, "reason": "not_found"}
    if req["status"] != "pending":
        return {"ok": False, "reason": "status_%s" % req["status"]}

    address = str(req["address"] or "")
    amount = float(req["amount"] or 0)
    tx_hash = None
    onchain = False

    if getattr(settings, "withdraw_onchain_enabled", True):
        from bot.services.ton_chain import (
            TonSendError,
            TonWalletNotConfigured,
            hot_wallet_configured,
            send_ton,
        )
        if not hot_wallet_configured():
            return {"ok": False, "reason": "no_hot_wallet"}
        try:
            tx_hash = await send_ton(address, amount, comment="cx_wd_%s" % request_id)
            onchain = True
        except TonWalletNotConfigured as exc:
            return {"ok": False, "reason": "no_hot_wallet", "error": str(exc)}
        except TonSendError as exc:
            logger.warning("on-chain settle failed req=%s: %s", request_id, exc)
            return {"ok": False, "reason": "send_failed", "error": str(exc)}
    else:
        tx_hash = "manual_%s" % request_id

    await complete_withdraw(request_id, tx_hash=tx_hash)
    return {
        "ok": True,
        "tx_hash": tx_hash,
        "onchain": onchain,
        "amount": amount,
        "address": address,
    }
