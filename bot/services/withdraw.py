"""Withdraw helpers: auto on-chain settlement under threshold."""
from __future__ import annotations

import logging
from typing import Any

from bot.config import settings
from bot.db.ledger import complete_withdraw, reject_withdraw

logger = logging.getLogger("cx.withdraw")


def withdraw_limits_for_kyc(kyc_level: int) -> dict[str, float]:
    """Per-transaction and daily limits by KYC level."""
    lvl = int(kyc_level or 0)
    min_ton = float(getattr(settings, "withdraw_min_ton", 1.0) or 1.0)
    if lvl >= 2:
        max_ton = float(getattr(settings, "withdraw_max_l2", 500.0) or 500.0)
        daily = float(getattr(settings, "withdraw_daily_max_l2", 1000.0) or 1000.0)
    elif lvl >= 1:
        max_ton = float(getattr(settings, "withdraw_max_l1", 50.0) or 50.0)
        daily = float(getattr(settings, "withdraw_daily_max_l1", 100.0) or 100.0)
    else:
        max_ton = float(getattr(settings, "withdraw_max_l0", 0.0) or 0.0)
        daily = float(getattr(settings, "withdraw_daily_max_l0", 0.0) or 0.0)
    return {"min": min_ton, "max": max_ton, "daily_max": daily, "kyc_level": float(lvl)}


async def get_withdraw_used_today(user_id: int) -> float:
    """Sum of withdraw requests created today (UTC) that are not rejected."""
    import aiosqlite
    async with aiosqlite.connect(settings.db_name) as db:
        cur = await db.execute(
            """
            SELECT COALESCE(SUM(amount), 0) FROM requests
            WHERE user_id = ?
              AND req_type = 'withdraw'
              AND status != 'rejected'
              AND date(created_at) = date('now')
            """,
            (user_id,),
        )
        row = await cur.fetchone()
    return float(row[0] or 0) if row else 0.0


def check_withdraw_amount(amount: float, kyc_level: int, *, used_today: float = 0.0) -> dict[str, Any]:
    """Return {ok:True} or {ok:False, error, min, max, daily_max, used_today, kyc_level}."""
    lim = withdraw_limits_for_kyc(kyc_level)
    amt = float(amount or 0)
    used = float(used_today or 0)
    if lim["max"] <= 0 or lim.get("daily_max", 0) <= 0:
        return {
            "ok": False,
            "error": "kyc_required",
            "min": lim["min"],
            "max": lim["max"],
            "daily_max": lim.get("daily_max", 0),
            "used_today": used,
            "kyc_level": int(lim["kyc_level"]),
        }
    if amt < lim["min"]:
        return {
            "ok": False,
            "error": "below_min",
            "min": lim["min"],
            "max": lim["max"],
            "daily_max": lim["daily_max"],
            "used_today": used,
            "kyc_level": int(lim["kyc_level"]),
        }
    if amt > lim["max"]:
        return {
            "ok": False,
            "error": "above_kyc_max",
            "min": lim["min"],
            "max": lim["max"],
            "daily_max": lim["daily_max"],
            "used_today": used,
            "kyc_level": int(lim["kyc_level"]),
        }
    if used + amt > lim["daily_max"] + 1e-9:
        return {
            "ok": False,
            "error": "daily_limit",
            "min": lim["min"],
            "max": lim["max"],
            "daily_max": lim["daily_max"],
            "used_today": used,
            "remaining_today": max(0.0, lim["daily_max"] - used),
            "kyc_level": int(lim["kyc_level"]),
        }
    return {
        "ok": True,
        "min": lim["min"],
        "max": lim["max"],
        "daily_max": lim["daily_max"],
        "used_today": used,
        "remaining_today": max(0.0, lim["daily_max"] - used),
        "kyc_level": int(lim["kyc_level"]),
    }



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
