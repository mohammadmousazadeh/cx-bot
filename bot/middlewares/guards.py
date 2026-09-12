"""Global access guards: maintenance & emergency freeze."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import settings
from bot.texts import TEXTS


class AccessGuardMiddleware(BaseMiddleware):
    """
    Blocks non-admin users when maintenance or emergency freeze is active.
    Applied to all message/callback updates.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if user is None:
            return await handler(event, data)

        is_admin = user.id == settings.admin_id

        if settings.maintenance_mode and not is_admin:
            text = TEXTS["fa"].get(
                "maintenance_msg",
                "🛠 ربات در حال بروزرسانی است. لطفاً کمی بعد دوباره تلاش کنید.",
            )
            if isinstance(event, Message):
                await event.answer(text, parse_mode="Markdown")
            elif isinstance(event, CallbackQuery):
                await event.answer("Maintenance mode", show_alert=True)
            return None

        if settings.emergency_freeze and not is_admin:
            # Allow read-only style commands; freeze is mainly for money ops
            # Handlers still re-check EMERGENCY_FREEZE for withdraw/transfer.
            pass

        return await handler(event, data)
