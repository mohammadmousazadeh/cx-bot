"""Reply and common keyboards."""
from __future__ import annotations

import random

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from bot.config import settings
from bot.services.webapp_token import make_webapp_token
from bot.texts import TEXTS


def _webapp_url(user_id: int | None = None) -> str:
    base = (settings.webapp_base_url or "").rstrip("/")
    rnd = random.randint(1000, 9999)
    url = f"{base}/app.html?v={rnd}"
    if user_id:
        exp, sig = make_webapp_token(int(user_id), settings.bot_token)
        url += f"&uid={int(user_id)}&exp={exp}&sig={sig}"
    return url


def get_main_dashboard_kb(
    lang: str = "fa",
    balance: float = 0.0,
    kyc: int = 0,
    is_admin: bool = False,
    user_id: int | None = None,
) -> ReplyKeyboardMarkup:
    t = TEXTS.get(lang, TEXTS["fa"])
    url = _webapp_url(user_id)
    kb: list[list[KeyboardButton]] = [
        [KeyboardButton(text=t["btn_miniapp_dash"], web_app=WebAppInfo(url=url))]
    ]
    if is_admin:
        kb.append([KeyboardButton(text=t["btn_admin_panel"])])
    return ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        is_persistent=True,
    )


def get_cancel_kb(lang: str = "fa") -> ReplyKeyboardMarkup:
    t = TEXTS.get(lang, TEXTS["fa"])
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t["btn_cancel"])]],
        resize_keyboard=True,
    )


def get_miniapp_inline_kb(lang: str = "fa", user_id: int | None = None) -> InlineKeyboardMarkup:
    t = TEXTS.get(lang, TEXTS["fa"])
    url = _webapp_url(user_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_miniapp_dash"], web_app=WebAppInfo(url=url))]
        ]
    )
