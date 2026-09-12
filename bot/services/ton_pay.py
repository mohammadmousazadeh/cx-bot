"""TON payment link generation and deposit verification (chain-backed)."""
from __future__ import annotations

from typing import Any, Dict

from bot.config import settings
from bot.services.ton_chain import (
    check_user_deposit,
    deposit_comment_for_user,
)


def generate_ton_payment(user_id: int, amount: float) -> Dict[str, Any]:
    wallet = settings.payment_wallet or settings.exchange_wallet
    if not wallet:
        raise RuntimeError("PAYMENT_WALLET / EXCHANGE_WALLET is not set in .env")

    comment = deposit_comment_for_user(user_id)
    nanotons = int(amount * 1_000_000_000)
    transfer_url = f"ton://transfer/{wallet}?amount={nanotons}&text={comment}"
    return {
        "address": wallet,
        "comment": comment,
        "amount": amount,
        "url": transfer_url,
        "min_deposit": float(getattr(settings, "min_deposit_ton", 1) or 1),
    }


async def check_ton_transaction(user_id: int, expected_amount: float = 0.0) -> bool:
    """
    Scan chain for this user's deposits and credit them.
    Returns True if any new amount was credited (or matched expected).
    """
    min_amt = float(getattr(settings, "min_deposit_ton", 1) or 1)
    credited = await check_user_deposit(user_id, min_amount=min_amt)
    if expected_amount > 0:
        return credited + 1e-9 >= expected_amount * 0.98
    return credited > 0
