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
