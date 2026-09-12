"""Reply and common keyboards."""
from __future__ import annotations

import random

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from bot.config import settings
from bot.texts import TEXTS


def get_main_dashboard_kb(
    lang: str = "fa",
    balance: float = 0.0,
    kyc: int = 0,
    is_admin: bool = False,
) -> ReplyKeyboardMarkup:
    t = TEXTS.get(lang, TEXTS["fa"])
    rnd = random.randint(1000, 9999)
    url = (
        f"{settings.webapp_base_url}/app.html"
        f"?v={rnd}&bal={balance}&kyc={kyc}"
    )
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
