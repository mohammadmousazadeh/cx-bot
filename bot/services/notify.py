"""User notifications via Telegram bot."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("cx.notify")


async def notify_user(bot, user_id: int, text: str, *, parse_mode: str = "Markdown") -> bool:
    if not bot or not user_id or not text:
        return False
    try:
        await bot.send_message(user_id, text, parse_mode=parse_mode)
        return True
    except Exception:
        logger.exception("notify_user failed uid=%s", user_id)
        return False


async def notify_trade_result(
    bot,
    user_id: int,
    *,
    product: str,
    won: bool,
    amount: float,
    payout: float = 0.0,
    symbol: str = "",
) -> None:
    if won:
        text = (
            f"**{product} WIN**\n"
            f"Symbol: `{symbol or '-'}\n"
            f"Stake: `{amount:g}` TON\n"
            f"Payout: `+{payout:g}` TON"
        )
    else:
        text = (
            f"**{product} LOSS**\n"
            f"Symbol: `{symbol or '-'}\n"
            f"Stake: `-{amount:g}` TON"
        )
    await notify_user(bot, user_id, text)


async def notify_kyc_result(bot, user_id: int, *, approved: bool, level: int = 2) -> None:
    if approved:
        text = f"**KYC approved**\nYour account is now **Level {level}**."
    else:
        text = "**KYC rejected**\nPlease resubmit clearer documents from the Mini App."
    await notify_user(bot, user_id, text)
