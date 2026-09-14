"""
Financial feature handlers — all money moves go through bot.db.ledger.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from bot.config import settings
from bot.db import get_user_data, log_security_event
from bot.db.ledger import (
    BusinessRuleError,
    InsufficientBalance,
    TxKind,
    create_stake,
    credit_ton,
    debit_ton,
    get_balances,
    grant_loan,
    transfer_p2p,
)
from bot.keyboards import get_cancel_kb, get_main_dashboard_kb
from bot.services.market import get_market_price
from bot.states import UserStates
from bot.texts import TEXTS

logger = logging.getLogger(__name__)

STAKE_PLANS = {
    7: 5.0,
    30: 12.0,
    90: 25.0,
}

LOAN_CREDIT = 20.0
LOAN_REPAY = 24.0
PREDICT_ENTRY = 5.0
PREDICT_WIN = 9.0
MIN_WITHDRAW = 150.0


async def staking_handler(message: Message, state: FSMContext) -> None:
    user_data = await get_user_data(message.from_user.id)
    lang = user_data[0]
    t = TEXTS.get(lang, TEXTS["fa"])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_stake_7"], callback_data="stake_plan_7")],
            [InlineKeyboardButton(text=t["btn_stake_30"], callback_data="stake_plan_30")],
            [InlineKeyboardButton(text=t["btn_stake_90"], callback_data="stake_plan_90")],
            [InlineKeyboardButton(text="📋 استیک‌های من / آزادسازی", callback_data="my_stakes")],
        ]
    )
    await message.answer(t["staking_desc"], reply_markup=kb, parse_mode="Markdown")


async def cb_stake_plan(callback: CallbackQuery, state: FSMContext) -> None:
    days = int(callback.data.split("_")[-1])
    user_data = await get_user_data(callback.from_user.id)
    lang = user_data[0]
    t = TEXTS.get(lang, TEXTS["fa"])
    await state.update_data(stake_days=days, stake_apy=STAKE_PLANS.get(days, 5.0))
    await callback.message.answer(
        t["stake_enter_amt"].format(days),
        reply_markup=get_cancel_kb(lang),
    )
    await state.set_state(UserStates.waiting_for_stake_amount)
    await callback.answer()


async def process_stake_amount(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    t = TEXTS.get(lang, TEXTS["fa"])

    if message.text in [TEXTS["fa"]["btn_cancel"], TEXTS["en"]["btn_cancel"]]:
        await state.clear()
        return await message.answer(
            t.get("cancelled", "Cancelled"),
            reply_markup=get_main_dashboard_kb(lang, balance, kyc, user_id == settings.admin_id, user_id=user_id),
        )

    try:
        amount = float(message.text.replace(",", "").strip())
    except ValueError:
        return await message.answer(t["stake_invalid"])

    if amount <= 0:
        return await message.answer(t["stake_invalid"])

    data = await state.get_data()
    days = int(data.get("stake_days", 7))
    apy = float(data.get("stake_apy", STAKE_PLANS.get(days, 5.0)))

    try:
        result, stake_id = await create_stake(user_id, amount, days=days, apy=apy)
    except InsufficientBalance:
        return await message.answer(t["insufficient_bal"])
    except Exception:
        logger.exception("stake failed")
        return await message.answer("❌ Error")

    end = datetime.utcnow() + timedelta(days=days)
    await log_security_event(user_id, f"Stake locked {amount} TON for {days}d #{stake_id}")
    await message.answer(
        t["stake_success"].format(amount, end.strftime("%Y-%m-%d")),
        reply_markup=get_main_dashboard_kb(
            lang, result.ton_balance, kyc, user_id == settings.admin_id
        , user_id=user_id),
        parse_mode="Markdown",
    )
    await state.clear()




async def cb_accept_loan(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    user_data = await get_user_data(user_id)
    lang, kyc = user_data[0], user_data[1]
    t = TEXTS.get(lang, TEXTS["fa"])

    try:
        result = await grant_loan(user_id, LOAN_CREDIT, LOAN_REPAY)
    except BusinessRuleError:
        return await callback.answer(t["loan_exists"], show_alert=True)
    except Exception:
        logger.exception("loan failed")
        return await callback.answer("Error", show_alert=True)

    await log_security_event(user_id, f"Loan credited {LOAN_CREDIT} TON")
    await callback.message.answer(
        t["loan_success"],
        reply_markup=get_main_dashboard_kb(
            lang, result.ton_balance, kyc, user_id == settings.admin_id
        , user_id=user_id),
    )
    await callback.answer()


async def predict_pool_handler(message: Message, state: FSMContext) -> None:
    user_data = await get_user_data(message.from_user.id)
    lang = user_data[0]
    t = TEXTS.get(lang, TEXTS["fa"])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t["btn_pred_btc_up"], callback_data="pred_BTC_up"),
                InlineKeyboardButton(text=t["btn_pred_btc_down"], callback_data="pred_BTC_down"),
            ],
            [
                InlineKeyboardButton(text=t["btn_pred_gold_up"], callback_data="pred_GOLD_up"),
                InlineKeyboardButton(text=t["btn_pred_gold_down"], callback_data="pred_GOLD_down"),
            ],
        ]
    )
    await message.answer(t["predict_desc"], reply_markup=kb, parse_mode="Markdown")


async def cb_predict(callback: CallbackQuery) -> None:
    """Short-horizon prediction settled against live market price."""
    import asyncio
    from bot.security import map_predict_symbol, rate_limit, require_not_frozen
    from bot.services.market import get_market_price

    user_id = callback.from_user.id
    user_data = await get_user_data(user_id)
    lang, kyc = user_data[0], user_data[1]
    t = TEXTS.get(lang, TEXTS["fa"])

    try:
        require_not_frozen(user_id)
    except PermissionError:
        return await callback.answer(t.get("freeze_active", "Frozen"), show_alert=True)

    if not rate_limit("predict:%s" % user_id, limit=10, window_sec=60):
        return await callback.answer("Too many requests", show_alert=True)

    parts = callback.data.split("_")
    if len(parts) < 3:
        return await callback.answer("Invalid", show_alert=True)
    asset = parts[1]
    direction = parts[2].lower()
    if direction not in ("up", "down"):
        return await callback.answer("Invalid direction", show_alert=True)

    symbol = map_predict_symbol(asset)
    entry = await get_market_price(symbol)
    if not entry or entry <= 0:
        return await callback.answer("Price unavailable", show_alert=True)

    try:
        await debit_ton(
            user_id,
            PREDICT_ENTRY,
            kind=TxKind.PREDICT_BET,
            meta={"asset": asset, "direction": direction, "entry": entry, "symbol": symbol},
        )
    except InsufficientBalance:
        return await callback.answer(t["insufficient_bal"], show_alert=True)

    await callback.message.answer(
        t["predict_wait"].format(asset, direction),
        parse_mode="Markdown",
    )
    await callback.answer()

    await asyncio.sleep(15)
    exit_price = await get_market_price(symbol)
    if not exit_price or exit_price <= 0:
        result = await credit_ton(
            user_id,
            PREDICT_ENTRY,
            kind=TxKind.PREDICT_WIN,
            meta={"refund": True, "reason": "oracle_fail", "asset": asset},
        )
        await callback.message.answer(
            "Settlement failed — stake refunded.",
            reply_markup=get_main_dashboard_kb(
                lang, result.ton_balance, kyc, user_id == settings.admin_id
            , user_id=user_id),
        )
        return

    went_up = exit_price >= entry
    won = (direction == "up" and went_up) or (direction == "down" and not went_up)
    price_line = "\n`%s` -> `%s`" % (format(entry, "g"), format(exit_price, "g"))
    if won:
        result = await credit_ton(
            user_id,
            PREDICT_WIN,
            kind=TxKind.PREDICT_WIN,
            meta={
                "asset": asset,
                "direction": direction,
                "entry": entry,
                "exit": exit_price,
            },
        )
        await callback.message.answer(
            t["predict_win"] + price_line,
            reply_markup=get_main_dashboard_kb(
                lang, result.ton_balance, kyc, user_id == settings.admin_id
            , user_id=user_id),
            parse_mode="Markdown",
        )
    else:
        ton, _ = await get_balances(user_id)
        await callback.message.answer(
            t["predict_loss"] + price_line,
            reply_markup=get_main_dashboard_kb(
                lang, ton, kyc, user_id == settings.admin_id
            , user_id=user_id),
            parse_mode="Markdown",
        )


async def daily_bonus_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, kyc = user_data[0], user_data[1]
    t = TEXTS.get(lang, TEXTS["fa"])

    from bot.db.connection import get_db

    async with get_db() as db:
        cur = await db.execute(
            "SELECT last_bonus_date FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        last = row["last_bonus_date"] if row else None

    if last:
        try:
            last_dt = datetime.fromisoformat(str(last))
            if datetime.utcnow() - last_dt < timedelta(hours=24):
                return await message.answer(t.get("bonus_wait", "⏳ Wait 24h"))
        except Exception:
            pass

    from bot.security import daily_bonus_amount, require_not_frozen, rate_limit
    try:
        require_not_frozen(user_id)
    except PermissionError:
        return await message.answer(t.get("freeze_active", "Frozen"))
    if not rate_limit(f"bonus:{user_id}", limit=5, window_sec=3600):
        return await message.answer("Too many requests")
    day_key = datetime.utcnow().strftime("%Y-%m-%d")
    reward = daily_bonus_amount(user_id, day_key)
    try:
        result = await credit_ton(
            user_id,
            reward,
            kind=TxKind.DAILY_BONUS,
            meta={"source": "wheel"},
            extra_user_updates={"last_bonus_date": datetime.utcnow().isoformat()},
        )
    except Exception:
        logger.exception("daily bonus failed")
        return await message.answer("❌ Error")

    await message.answer(
        t.get("spin_wheel_win", "🎰 You won `{0} TON`").format(reward),
        reply_markup=get_main_dashboard_kb(
            lang, result.ton_balance, kyc, user_id == settings.admin_id
        , user_id=user_id),
        parse_mode="Markdown",
    )


async def process_transfer_with_ledger(
    message: Message,
    state: FSMContext,
    target_id: int,
    amount: float,
) -> None:
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, kyc = user_data[0], user_data[1]
    t = TEXTS.get(lang, TEXTS["fa"])

    try:
        sender_res, _ = await transfer_p2p(user_id, target_id, amount)
    except InsufficientBalance:
        await message.answer(t["insufficient_bal"])
        await state.clear()
        return
    except BusinessRuleError as e:
        await message.answer(str(e))
        await state.clear()
        return
    except Exception:
        logger.exception("p2p failed")
        await message.answer("❌ Error")
        await state.clear()
        return

    await log_security_event(user_id, f"P2P {amount} TON -> {target_id}")
    await message.answer(
        t.get("stake_success", "✅ Done").split(".")[0] + f"\nP2P `{amount:g}` TON → `{target_id}`",
        reply_markup=get_main_dashboard_kb(
            lang, sender_res.ton_balance, kyc, user_id == settings.admin_id
        , user_id=user_id),
        parse_mode="Markdown",
    )
    try:
        await message.bot.send_message(
            target_id,
            f"💸 +`{amount:g}` TON from `{user_id}`",
            parse_mode="Markdown",
        )
    except Exception:
        pass
    await state.clear()


# ---------------------------------------------------------------------------
# Stake unlock & loan repay
# ---------------------------------------------------------------------------

async def my_stakes_handler(message: Message, state: FSMContext) -> None:
    """List active stakes with unlock buttons when matured."""
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang = user_data[0]
    t = TEXTS.get(lang, TEXTS["fa"])

    from bot.db.ledger import list_user_stakes

    stakes = await list_user_stakes(user_id, include_closed=False)
    if not stakes:
        await message.answer(
            t.get("staking_desc", "🏦 Staking") + "\n\n📭 هیچ استیک فعالی ندارید.",
            parse_mode="Markdown",
        )
        return await staking_handler(message, state)

    lines = ["🏦 **استیک‌های فعال شما**\n"]
    buttons = []
    now = datetime.utcnow()
    for s in stakes:
        end = s.get("end_date")
        try:
            end_dt = datetime.fromisoformat(str(end).replace("Z", ""))
        except Exception:
            end_dt = now
        matured = now >= end_dt
        status_icon = "✅ آماده آزادسازی" if matured else f"⏳ تا {end_dt.strftime('%Y-%m-%d')}"
        lines.append(
            f"• #{s['id']} — `{float(s['amount']):g} TON` | APY {s['apy']}% | {status_icon}"
        )
        if matured:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"🔓 آزادسازی #{s['id']}",
                        callback_data=f"unlock_stake_{s['id']}",
                    )
                ]
            )

    buttons.append(
        [InlineKeyboardButton(text=t.get("btn_stake_7", "استیک جدید"), callback_data="stake_plan_7")]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("\n".join(lines), reply_markup=kb, parse_mode="Markdown")


async def cb_unlock_stake(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    user_data = await get_user_data(user_id)
    lang, kyc = user_data[0], user_data[1]
    t = TEXTS.get(lang, TEXTS["fa"])
    stake_id = int(callback.data.split("_")[-1])

    from bot.db.ledger import unlock_stake

    try:
        result, principal, reward = await unlock_stake(user_id, stake_id)
    except BusinessRuleError as e:
        msg = str(e)
        if msg.startswith("stake_not_matured"):
            return await callback.answer("هنوز موعد آزادسازی نرسیده است.", show_alert=True)
        return await callback.answer(msg, show_alert=True)
    except Exception:
        logger.exception("unlock failed")
        return await callback.answer("Error", show_alert=True)

    await log_security_event(
        user_id, f"Stake unlock #{stake_id} principal={principal} reward={reward}"
    )
    text = (
        f"✅ **استیک #{stake_id} آزاد شد**\n"
        f"اصل: `{principal:g} TON`\n"
        f"سود: `{reward:g} TON`\n"
        f"موجودی: `{result.ton_balance:g} TON`"
    )
    await callback.message.answer(
        text,
        reply_markup=get_main_dashboard_kb(
            lang, result.ton_balance, kyc, user_id == settings.admin_id
        , user_id=user_id),
        parse_mode="Markdown",
    )
    await callback.answer("Unlocked")


async def cb_repay_loan(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    user_data = await get_user_data(user_id)
    lang, loan_amt, kyc = user_data[0], user_data[3], user_data[1]
    t = TEXTS.get(lang, TEXTS["fa"])

    from bot.db.ledger import repay_loan

    try:
        result = await repay_loan(user_id)
    except InsufficientBalance:
        return await callback.answer(t["insufficient_bal"], show_alert=True)
    except BusinessRuleError as e:
        return await callback.answer(str(e), show_alert=True)
    except Exception:
        logger.exception("repay failed")
        return await callback.answer("Error", show_alert=True)

    await log_security_event(user_id, f"Loan repaid {loan_amt}")
    await callback.message.answer(
        f"✅ وام تسویه شد.\nموجودی: `{result.ton_balance:g} TON`",
        reply_markup=get_main_dashboard_kb(
            lang, result.ton_balance, kyc, user_id == settings.admin_id
        , user_id=user_id),
        parse_mode="Markdown",
    )
    await callback.answer("Repaid")


# Enhance loan_handler UI with repay button when loan active — patch via wrapper
async def loan_handler(message: Message) -> None:
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, loan_amt, balance, kyc = user_data[0], user_data[3], user_data[4], user_data[1]
    t = TEXTS.get(lang, TEXTS["fa"])

    if loan_amt and float(loan_amt) > 0:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"💳 بازپرداخت {float(loan_amt):g} TON",
                        callback_data="repay_loan",
                    )
                ]
            ]
        )
        return await message.answer(
            t["loan_active"].format(loan_amt),
            reply_markup=kb,
            parse_mode="Markdown",
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_loan_accept"], callback_data="accept_loan")]
        ]
    )
    await message.answer(t["loan_desc"], reply_markup=kb, parse_mode="Markdown")
