"""
All bot handlers registered on a single Router.
Split further into domain modules as the team grows.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta

import aiosqlite
import aiohttp
from aiogram import F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from bot.config import settings
from bot.db import get_user_data, init_db, is_disposable_email, log_security_event
from bot.db.connection import get_db
from bot.db.users import ensure_user, set_user_fields
from bot.db.ledger import (
    TxKind,
    InsufficientBalance,
    DuplicateTransaction,
    BusinessRuleError,
    credit_ton,
    debit_ton,
    credit_deposit,
    hold_withdraw,
    complete_withdraw,
    reject_withdraw,
    swap_ton_to_usdt,
    transfer_p2p,
    create_stake,
)
from bot.handlers.finance import (
    staking_handler,
    loan_handler,
    predict_pool_handler,
    daily_bonus_handler,
    cb_stake_plan,
    process_stake_amount,
    cb_accept_loan,
    cb_predict,
    my_stakes_handler,
    cb_unlock_stake,
    cb_repay_loan,
)
from bot.keyboards import get_cancel_kb, get_main_dashboard_kb, get_miniapp_inline_kb
from bot.services.market import get_market_price
from bot.services.pnl_card import generate_pnl_card
from bot.services.ton_pay import check_ton_transaction, generate_ton_payment
from bot.states import UserStates
from bot.texts import TEXTS

router = Router(name="main")

async def _maybe_notify_app_update(message: Message) -> None:
    """Once per version: tell user the mini-app/bot was updated."""
    try:
        from bot.config import settings as _s
        from bot.db.users import get_user_seen_version, mark_app_version_seen
        ver = getattr(_s, "app_version", "1.0.0") or "1.0.0"
        seen = await get_user_seen_version(message.from_user.id)
        if seen == ver:
            return
        changelog = getattr(_s, "app_changelog", "") or ""
        lang = "fa"
        try:
            ud = await get_user_data(message.from_user.id)
            lang = ud[0] or "fa"
        except Exception:
            pass
        if lang == "fa":
            text = f"بروزرسانی CX\nنسخه جدید: `{ver}`"
            if changelog:
                text += f"\n\n{changelog[:800]}"
            text += "\n\nمینی‌اپ را از منو دوباره باز کنید."
        else:
            text = f"CX updated\nNew version: `{ver}`"
            if changelog:
                text += f"\n\n{changelog[:800]}"
            text += "\n\nReopen the Mini App from the menu."
        await message.answer(text, parse_mode="Markdown")
        await mark_app_version_seen(message.from_user.id, ver)
    except Exception:
        pass


# Finance callbacks / FSM (implemented in handlers.finance)
router.callback_query.register(cb_stake_plan, F.data.startswith("stake_plan_"), StateFilter("*"))
router.message.register(process_stake_amount, StateFilter(UserStates.waiting_for_stake_amount))
router.callback_query.register(cb_accept_loan, F.data == "accept_loan", StateFilter("*"))
router.callback_query.register(cb_predict, F.data.startswith("pred_"), StateFilter("*"))
router.callback_query.register(cb_unlock_stake, F.data.startswith("unlock_stake_"), StateFilter("*"))
router.callback_query.register(cb_repay_loan, F.data == "repay_loan", StateFilter("*"))

@router.callback_query(F.data == "my_stakes", StateFilter("*"))
async def cb_my_stakes(callback: CallbackQuery, state: FSMContext):
    await my_stakes_handler(callback.message, state)
    await callback.answer()




async def check_ton_dep_from_webapp(message: Message) -> None:
    """Scan chain for deposits tagged with this user (cx_<id>)."""
    user_id = message.from_user.id
    lang = (await get_user_data(user_id))[0]
    t = TEXTS[lang]
    wait_msg = await message.answer(t.get("checking_net", "⏳ در حال بررسی شبکه..."))
    result = 0.0
    try:
        from bot.services.ton_chain import check_user_deposit
        min_amt = float(getattr(settings, "min_deposit_ton", 1) or 1)
        result = await check_user_deposit(user_id, min_amount=min_amt)
    except Exception:
        logging.exception("deposit scan failed")
        result = -1.0



@router.message(CommandStart(), StateFilter("*"))
async def start_cmd(message: Message, state: FSMContext):
    await _maybe_notify_app_update(message)
    user_id = message.from_user.id
    args = message.text.split()
    await state.clear()
    
    if settings.emergency_freeze and user_id != settings.admin_id:
        return await message.answer(TEXTS["fa"]["freeze_active"])

    async with aiosqlite.connect(settings.db_name) as db:
        cursor = await db.execute("SELECT lang, kyc_level FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        
        if not row:
            lvl = 2 if user_id == settings.admin_id else 0
            referrer_id = None
            if len(args) > 1:
                payload = args[1].strip()
                if payload.isdigit() and int(payload) != user_id:
                    ref = int(payload)
                    c_chk = await db.execute("SELECT join_date FROM users WHERE user_id = ?", (ref,))
                    if await c_chk.fetchone():
                        referrer_id = ref
                else:
                    code = payload.upper().replace("REF_", "CX-")
                    if not code.startswith("CX-") and len(code) >= 4:
                        code = "CX-" + code
                    c_chk = await db.execute(
                        "SELECT user_id FROM users WHERE upper(COALESCE(referral_code,'')) = ? LIMIT 1",
                        (code,),
                    )
                    row_ref = await c_chk.fetchone()
                    if row_ref and int(row_ref[0]) != user_id:
                        referrer_id = int(row_ref[0])
                        await db.execute(
                            "UPDATE users SET referral_count = COALESCE(referral_count, 0) + 1 WHERE user_id = ?",
                            (referrer_id,),
                        )

            await db.execute('INSERT INTO users (user_id, referrer_id, balance, kyc_level) VALUES (?, ?, ?, ?)', (user_id, referrer_id, 0.0, lvl))
            await db.commit()
            try:
                from bot.db.users import ensure_referral_code
                await ensure_referral_code(user_id)
            except Exception:
                pass
            try:
                await credit_ton(user_id, settings.welcome_bonus_ton, kind=TxKind.WELCOME_BONUS, meta="signup", idempotency_key=f"welcome:{user_id}")
            except Exception:
                pass
            await log_security_event(user_id, "User Registered")
            
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🇮🇷 فارسی", callback_data="setlang_fa"), InlineKeyboardButton(text="🇺🇸 English", callback_data="setlang_en")]])
            return await message.answer("🌍 لطفاً زبان خود را انتخاب کنید / Please choose your language:", reply_markup=kb)
        
        lang, kyc = row[0] or "fa", row[1]
        t = TEXTS.get(lang, TEXTS["fa"])
        if user_id == settings.admin_id:
            await db.execute('UPDATE users SET kyc_level = 2 WHERE user_id = ?', (user_id,))
            await db.commit()
            user_data = await get_user_data(user_id)
            return await message.answer(t["dashboard_ready"], reply_markup=get_main_dashboard_kb(lang, user_data[4], user_data[1], True, user_id=user_id))

        if kyc == 0:
            kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t["btn_send_phone"], request_contact=True)]], resize_keyboard=True)
            return await message.answer(t["mandatory_phone_msg"], reply_markup=kb, parse_mode="Markdown")

    t = TEXTS.get(lang, TEXTS["fa"])
    user_data = await get_user_data(user_id)
    try:
        from bot.db.users import ensure_referral_code
        await ensure_referral_code(user_id)
    except Exception:
        pass
    await message.answer(t["dashboard_ready"], reply_markup=get_main_dashboard_kb(lang, user_data[4], user_data[1], user_id == settings.admin_id, user_id=user_id))

@router.callback_query(F.data.startswith("setlang_"), StateFilter("*"))
async def cb_set_language(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    lang = callback.data.split("_")[1]
    user_id = callback.from_user.id
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, user_id))
        await db.commit()
        c = await db.execute("SELECT kyc_level FROM users WHERE user_id = ?", (user_id,))
        kyc = (await c.fetchone())[0]
        
    await callback.message.delete()
    t = TEXTS[lang]
    if kyc == 0 and user_id != settings.admin_id:
        await callback.message.answer(t["welcome_bonus"])
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t["btn_send_phone"], request_contact=True)]], resize_keyboard=True)
        return await callback.message.answer(t["mandatory_phone_msg"], reply_markup=kb, parse_mode="Markdown")
        
    user_data = await get_user_data(user_id)
    await callback.message.answer(t["dashboard_ready"], reply_markup=get_main_dashboard_kb(lang, user_data[4], user_data[1], user_id == settings.admin_id, user_id=user_id))

@router.message(F.contact, StateFilter("*"))
async def phone_contact_handler(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang = user_data[0]
    t = TEXTS.get(lang, TEXTS["fa"])
    
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE users SET phone = ?, kyc_level = 1 WHERE user_id = ?", (message.contact.phone_number, user_id))
        await db.commit()
        # referral reward when invitee verifies phone (L1)
        try:
            cur_r = await db.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,))
            rr = await cur_r.fetchone()
            if rr and rr[0]:
                reward = float(getattr(settings, "referral_l1_reward", 1.0) or 0)
                if reward > 0:
                    await credit_ton(
                        int(rr[0]),
                        reward,
                        kind=TxKind.MISSION_REWARD,
                        meta={"type": "referral_l1", "invitee": user_id, "amount": reward},
                        idempotency_key=f"referral_l1:{user_id}",
                    )
                    try:
                        await message.bot.send_message(
                            int(rr[0]),
                            f"Referral reward +{reward:g} TON (user {user_id} verified phone).",
                        )
                    except Exception:
                        pass
        except Exception:
            pass
        
    await log_security_event(user_id, "Phone Verified")
    await message.answer(t["phone_saved_msg"], reply_markup=ReplyKeyboardRemove())
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_upgrade_kyc"], callback_data="start_kyc_flow")],
        [InlineKeyboardButton(text=t["btn_skip_kyc"], callback_data="skip_kyc_to_dash")]
    ])
    await message.answer(t["kyc_decision_msg"], reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "skip_kyc_to_dash", StateFilter("*"))
async def cb_skip_kyc(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_data = await get_user_data(callback.from_user.id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    await callback.message.delete()
    await callback.message.answer(TEXTS[lang]["dashboard_ready"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, callback.from_user.id == settings.admin_id, user_id=callback.from_user.id))

@router.callback_query(F.data == "start_kyc_flow", StateFilter("*"))
async def cb_start_kyc_flow(callback: CallbackQuery, state: FSMContext):
    user_data = await get_user_data(callback.from_user.id)
    lang = user_data[0]
    t = TEXTS[lang]
    await callback.message.answer(t["kyc_ask_email"], reply_markup=get_cancel_kb(lang))
    await state.set_state(UserStates.waiting_for_kyc_email)

@router.message(StateFilter(UserStates.waiting_for_kyc_email))
async def process_kyc_email(message: Message, state: FSMContext):
    user_data = await get_user_data(message.from_user.id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    t = TEXTS[lang]
    
    if message.text in [TEXTS["fa"]["btn_cancel"], TEXTS["en"]["btn_cancel"]]:
        await state.clear()
        return await message.answer(t["cancelled"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, message.from_user.id == settings.admin_id, user_id=message.from_user.id))

    email = message.text.strip().lower()
    if await is_disposable_email(email):
        return await message.answer(t["email_invalid"])

    await state.update_data(kyc_email=email)
    ask = t.get("kyc_passport_ask") or t["kyc_upload_ask"]
    await message.answer(ask, reply_markup=get_cancel_kb(lang))
    await state.set_state(UserStates.waiting_for_kyc_passport)


@router.message(F.photo, StateFilter(UserStates.waiting_for_kyc_passport))
async def process_kyc_passport(message: Message, state: FSMContext):
    user_data = await get_user_data(message.from_user.id)
    lang = user_data[0]
    t = TEXTS[lang]
    file_id = message.photo[-1].file_id
    await state.update_data(kyc_passport_file_id=file_id)
    msg = t.get("kyc_selfie_ask") or (
        "اکنون یک سلفی واضح از چهره خود ارسال کنید." if lang == "fa" else "Now send a clear selfie of your face."
    )
    await message.answer(msg, reply_markup=get_cancel_kb(lang))
    await state.set_state(UserStates.waiting_for_kyc_selfie)


@router.message(F.photo, StateFilter(UserStates.waiting_for_kyc_selfie))
async def process_kyc_selfie(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    t = TEXTS[lang]
    data = await state.get_data()
    email = data.get("kyc_email", "Not Set")
    passport_id = data.get("kyc_passport_file_id")
    selfie_id = message.photo[-1].file_id
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE users SET email = ? WHERE user_id = ?", (email, user_id))
        try:
            await db.execute(
                """
                INSERT INTO kyc_submissions (user_id, email, passport_file_id, selfie_file_id, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (user_id, email, passport_id, selfie_id),
            )
        except Exception:
            pass
        await db.commit()
    await message.answer(
        t.get("kyc_pending_new") or "Submitted for review.",
        reply_markup=get_main_dashboard_kb(lang, balance, kyc, user_id == settings.admin_id, user_id=user_id),
    )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Approve L2", callback_data=f"adm_kyc_accept_{user_id}"),
        InlineKeyboardButton(text="Reject", callback_data=f"adm_kyc_reject_{user_id}"),
    ]])
    try:
        cap = f"KYC L2\nUser: `{user_id}`\nEmail: `{email}`"
        if passport_id:
            await message.bot.send_photo(settings.admin_id, photo=passport_id, caption=cap + "\nPassport", parse_mode="Markdown")
        await message.bot.send_photo(
            settings.admin_id, photo=selfie_id, caption=cap + "\nSelfie",
            reply_markup=admin_kb, parse_mode="Markdown",
        )
    except Exception:
        pass
    await log_security_event(user_id, "KYC passport+selfie")
    await state.clear()


@router.message(F.photo, StateFilter(UserStates.waiting_for_kyc_photo))
async def process_kyc_photo_legacy(message: Message, state: FSMContext):
    await process_kyc_passport(message, state)



@router.callback_query(F.data.startswith("adm_kyc_accept_"), StateFilter("*"))
async def cb_admin_kyc_accept(callback: CallbackQuery):
    if callback.from_user.id != settings.admin_id: return
    target_id = int(callback.data.split("_")[3])
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE users SET kyc_level = 2 WHERE user_id = ?", (target_id,))
        try:
            await db.execute(
                "UPDATE kyc_submissions SET status='approved', reviewed_at=CURRENT_TIMESTAMP "
                "WHERE user_id=? AND status='pending'",
                (target_id,),
            )
        except Exception:
            pass
        await db.commit()
    try:
        from bot.db.users import pay_referral_l2_reward
        await pay_referral_l2_reward(target_id)
    except Exception:
        pass
    await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n✅ **تأیید شد.**", reply_markup=None)
    try:
        target_lang = (await get_user_data(target_id))[0]
        msg = "مدارک هویتی شما تأیید شد و حسابتان به سطح ۲ ارتقا یافت." if target_lang == "fa" else "KYC approved! Upgraded to Level 2."
        await callback.bot.send_message(target_id, msg)
    except Exception:
        pass

@router.callback_query(F.data.startswith("adm_kyc_reject_"), StateFilter("*"))
async def cb_admin_kyc_reject(callback: CallbackQuery):
    if callback.from_user.id != settings.admin_id: return
    target_id = int(callback.data.split("_")[3])
    await callback.message.edit_caption(caption=f"{callback.message.caption}\n\n**رد شد.**", reply_markup=None)
    try:
        target_lang = (await get_user_data(target_id))[0]
        msg = "مدارک شما رد شد. لطفاً تصویر واضح و ایمیل معتبر ارسال فرمایید." if target_lang == "fa" else "KYC Rejected."
        await callback.bot.send_message(target_id, msg)
    except Exception:
        pass

async def pnl_card_handler(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    async with aiosqlite.connect(settings.db_name) as db:
        c = await db.execute("SELECT balance, join_date FROM users WHERE user_id = ?", (user_id,))
        row = await c.fetchone()
        
    current_bal = float(row[0]) if row and row[0] is not None else 0.0
    join_date = str(row[1]) if row and row[1] else "2026-01-01"
    
    initial_base = 5.0
    real_pnl = ((current_bal - initial_base) / initial_base) * 100.0 if initial_base > 0 else 0.0
    
    try:
        img_buf = generate_pnl_card(user_id, real_pnl, current_bal, join_date)
        input_file = BufferedInputFile(img_buf.getvalue(), filename="cx_pnl.png")
        
        sign = "+" if real_pnl >= 0 else ""
        caption = (
            f"📊 <b>کارنامه عملکرد معاملاتی CX Exchange</b>\n\n"
            f"🔹 بازدهی کل: <code>{sign}{real_pnl:,.2f}%</code>\n"
            f"💼 حجم موجودی نقد: <code>{current_bal:g} TON</code>\n\n"
            f"🔗 لینک ورود اختصاصی:\nhttps://t.me/cx_bot?start={user_id}"
        )
        await message.answer_photo(photo=input_file, caption=caption, parse_mode="HTML")
    except Exception as e:
        logging.exception("PNL Error")
        await message.answer(f"خطا در تولید کارت: {e}")

@router.message(F.web_app_data, StateFilter("*"))
async def web_app_data_handler(message: Message, state: FSMContext):
    await state.clear()
    data = message.web_app_data.data
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    t = TEXTS.get(lang, TEXTS["fa"])

    if data.startswith("cmd_"):
        command_text = data.replace("cmd_", "")
        
        if command_text == "KYC":
            if kyc == 2:
                return await message.answer("✅ حساب کاربری شما تایید شده (سطح ۲) است.")
            await message.answer(t["kyc_ask_email"], reply_markup=get_cancel_kb(lang))
            await state.set_state(UserStates.waiting_for_kyc_email)
        elif "P&L" in command_text or "کارت سود" in command_text:
            await pnl_card_handler(message, state)
        elif "مرکز امنیت حساب" in command_text:
            await security_center_handler(message, state)
        elif "صندوق استیکینگ" in command_text:
            await staking_handler(message, state)
        elif "وام فلش" in command_text:
            await loan_handler(message)
        elif "انتقال P2P" in command_text:
            await p2p_transfer_handler(message, state)
        elif "پشتیبانی" in command_text:
            await support_menu_handler(message, state)
        elif "پیش‌بینی" in command_text:
            await predict_pool_handler(message, state)
        elif "گردونه شانس" in command_text:
            await daily_bonus_handler(message, state)
        elif "مرکز ماموریت‌ها" in command_text:
            await missions_handler(message, state)
        elif "استعلام شبکه" in command_text:
            await check_ton_dep_from_webapp(message)

    elif data.startswith("swap_"):
        try:
            pay_amount = float(data.split("_")[1])
            rate = await get_market_price("TONUSDT") or 5.28
            
            if pay_amount <= 0 or pay_amount > balance:
                return await message.answer(t["insufficient_bal"])
                
            fee = pay_amount * 0.001
            swapped_ton = pay_amount - fee
            received_usdt = swapped_ton * rate
            
            try:
                ledger_res = await swap_ton_to_usdt(
                    user_id,
                    pay_amount,
                    received_usdt,
                    rate=rate,
                    fee_ton=fee,
                )
            except InsufficientBalance:
                return await message.answer(t["insufficient_bal"])
            
            new_balance = ledger_res.ton_balance
            receipt = (
                "✅ **تراکنش سواپ با موفقیت در شبکه پردازش شد**\n\n"
                f"🔹 پرداخت شده: `{pay_amount:g} TON`\n"
                f"🔹 کارمزد پروتکل (۰.۱٪): `{fee:g} TON`\n"
                f"🔹 نرخ اجرایی: `{rate:,.4f}`\n"
                f"💵 واریز به بالانس تتر: `{received_usdt:,.2f} USDT`\n\n"
                "دارایی در کیف پول شما بروزرسانی گردید."
            ) if lang == "fa" else (
                "✅ **Swap Executed Successfully**\n\n"
                f"🔹 Swapped: `{pay_amount:g} TON`\n"
                f"🔹 Pool Fee (0.1%): `{fee:g} TON`\n"
                f"🔹 Price: `{rate:,.4f}`\n"
                f"💵 Credited: `{received_usdt:,.2f} USDT`"
            )
            await message.answer(receipt, reply_markup=get_main_dashboard_kb(lang, new_balance, kyc, user_id == settings.admin_id, user_id=user_id), parse_mode="Markdown")
        except Exception:
            await message.answer("خطا در پردازش سواپ.")

    elif data == "withdraw_req":
        loan, pin, cooldown = user_data[3], user_data[6], user_data[7]
        
        if settings.emergency_freeze: return await message.answer(t["freeze_active"])
        if loan > 0: return await message.answer(t["with_blocked"])
        
        if cooldown:
            try:
                if datetime.now() < datetime.strptime(str(cooldown), "%Y-%m-%d %H:%M:%S.%f"):
                    return await message.answer(t["cooldown_error"])
            except Exception: pass
                
        if not pin:
            return await message.answer("⚠️ لطفاً ابتدا از بخش تنظیمات -> مرکز امنیت حساب، پین ۴ رقمی تعریف کنید.")
            
        await message.answer(t["with_addr_ask"], reply_markup=get_cancel_kb(lang), parse_mode="Markdown")
        await state.set_state(UserStates.waiting_for_withdraw_address)

async def missions_handler(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang = user_data[0]
    t = TEXTS[lang]
    
    async with aiosqlite.connect(settings.db_name) as db:
        c = await db.execute("SELECT task_channel, task_invite, task_youtube FROM users WHERE user_id = ?", (user_id,))
        row = await c.fetchone()
        ch_done = "✅" if row and row[0] == 1 else "🎁"
        inv_done = "✅" if row and row[1] == 1 else "🎁"
        yt_done = "✅" if row and len(row) > 2 and row[2] == 1 else ("❌" if row and len(row) > 2 and row[2] == -1 else "🎁")
        
        c2 = await db.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,))
        refs = (await c2.fetchone())[0]

    btn_join_ch_text = "📢 ورود به کانال تلگرام" if lang == "fa" else "📢 Join Channel"
    btn_watch_yt_text = "📺 تماشای ویدیوی یوتیوب" if lang == "fa" else "📺 Watch YouTube"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_join_ch_text, url=f"https://t.me/{settings.channel_id.replace('@', '')}")],
        [InlineKeyboardButton(text=t["btn_miss_ch"].format(ch_done), callback_data="check_task_channel")],
        [InlineKeyboardButton(text=btn_watch_yt_text, url=settings.youtube_video_url)],
        [InlineKeyboardButton(text=t["btn_miss_yt"].format(yt_done), callback_data="start_task_youtube")],
        [InlineKeyboardButton(text=t["btn_miss_inv"].format(inv_done), callback_data="check_task_invite")]
    ])
    await message.answer(t["mission_desc"], reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "start_task_youtube", StateFilter("*"))
async def cb_start_task_youtube(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    user_data = await get_user_data(user_id)
    lang = user_data[0]
    t = TEXTS[lang]
    
    async with aiosqlite.connect(settings.db_name) as db:
        c = await db.execute("SELECT task_youtube FROM users WHERE user_id = ?", (user_id,))
        row = await c.fetchone()
        if row and row[0] == 1: return await callback.answer(t["miss_claimed"], show_alert=True)
        if row and row[0] == -1: return await callback.answer(t["miss_yt_failed_already"], show_alert=True)
        
    btn_join_yt_text = "📺 ورود به یوتیوب" if lang == "fa" else "📺 Open YouTube"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_join_yt_text, url=settings.youtube_video_url)],
        [InlineKeyboardButton(text="🎯 POC", callback_data="yt_ans_poc")],
        [InlineKeyboardButton(text="📈 VAH", callback_data="yt_ans_vah")],
        [InlineKeyboardButton(text="📉 VAL", callback_data="yt_ans_val")],
        [InlineKeyboardButton(text=t["btn_back_main"], callback_data="back_to_missions")]
    ])
    await callback.message.edit_text(t["yt_quiz_title"], reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data.startswith("yt_ans_"), StateFilter("*"))
async def cb_yt_answer(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    user_data = await get_user_data(user_id)
    lang = user_data[0]
    t = TEXTS[lang]
    selected_answer = callback.data.split("_")[2]
    
    async with aiosqlite.connect(settings.db_name) as db:
        c = await db.execute("SELECT task_youtube FROM users WHERE user_id = ?", (user_id,))
        row = await c.fetchone()
        if row and row[0] == 1: return await callback.answer(t["miss_claimed"], show_alert=True)
        if row and row[0] == -1: return await callback.answer(t["miss_yt_failed_already"], show_alert=True)
        
        if selected_answer == "poc":
            await credit_ton(user_id, 3.0, kind=TxKind.MISSION_REWARD, meta="youtube", extra_user_updates={"task_youtube": 1}, idempotency_key=f"mission_yt:{user_id}")
            await callback.answer(t["miss_yt_success"], show_alert=True)
        else:
            await db.execute("UPDATE users SET task_youtube = -1 WHERE user_id = ?", (user_id,))
            await db.commit()
            await callback.answer(t["miss_yt_err_burn"], show_alert=True)
            
    await callback.message.delete()
    await missions_handler(callback.message, state)

@router.callback_query(F.data == "back_to_missions", StateFilter("*"))
async def cb_back_to_missions(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await missions_handler(callback.message, state)

@router.callback_query(F.data == "check_task_channel", StateFilter("*"))
async def cb_check_task_channel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    user_data = await get_user_data(user_id)
    lang = user_data[0]
    t = TEXTS[lang]
    
    async with aiosqlite.connect(settings.db_name) as db:
        c = await db.execute("SELECT task_channel FROM users WHERE user_id = ?", (user_id,))
        if (await c.fetchone())[0] == 1: return await callback.answer(t["miss_claimed"], show_alert=True)
    try:
        member = await callback.bot.get_chat_member(settings.channel_id, user_id)
        if member.status in ['member', 'administrator', 'creator']:
            async with aiosqlite.connect(settings.db_name) as db:
                await credit_ton(user_id, 2.0, kind=TxKind.MISSION_REWARD, meta="channel", extra_user_updates={"task_channel": 1}, idempotency_key=f"mission_ch:{user_id}")
            await callback.answer(t["miss_ch_success"], show_alert=True)
            await missions_handler(callback.message, state)
        else:
            await callback.answer(t["miss_ch_err"], show_alert=True)
    except Exception:
        err_msg = "ربات در کانال ادمین نیست." if lang == "fa" else "Admin permission required in channel."
        await callback.answer(err_msg, show_alert=True)

@router.callback_query(F.data == "check_task_invite", StateFilter("*"))
async def cb_check_task_invite(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    user_data = await get_user_data(user_id)
    lang = user_data[0]
    t = TEXTS[lang]
    
    async with aiosqlite.connect(settings.db_name) as db:
        c = await db.execute("SELECT task_invite FROM users WHERE user_id = ?", (user_id,))
        if (await c.fetchone())[0] == 1: return await callback.answer(t["miss_claimed"], show_alert=True)
        c2 = await db.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,))
        refs = (await c2.fetchone())[0]
        if refs >= 3:
            await credit_ton(user_id, 5.0, kind=TxKind.MISSION_REWARD, meta="invite", extra_user_updates={"task_invite": 1}, idempotency_key=f"mission_inv:{user_id}")
            await callback.answer(t["miss_inv_success"], show_alert=True)
            await missions_handler(callback.message, state)
        else:
            await callback.answer(t["miss_inv_err"].format(refs), show_alert=True)

async def support_menu_handler(message: Message, state: FSMContext):
    await state.clear()
    user_data = await get_user_data(message.from_user.id)
    lang = user_data[0]
    t = TEXTS[lang]
    contact = "پیام مستقیم به پشتیبانی" if lang == "fa" else "Contact support"
    back_note = ""  # unused
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=FAQ_DATA["faq_deposit"][f"title_{lang}"], callback_data="faq_deposit"),
            InlineKeyboardButton(text=FAQ_DATA["faq_withdraw"][f"title_{lang}"], callback_data="faq_withdraw"),
        ],
        [
            InlineKeyboardButton(text=FAQ_DATA["faq_binary"][f"title_{lang}"], callback_data="faq_binary"),
            InlineKeyboardButton(text=FAQ_DATA["faq_swap"][f"title_{lang}"], callback_data="faq_swap"),
        ],
        [
            InlineKeyboardButton(text=FAQ_DATA["faq_sniper"][f"title_{lang}"], callback_data="faq_sniper"),
            InlineKeyboardButton(text=FAQ_DATA["faq_prop"][f"title_{lang}"], callback_data="faq_prop"),
        ],
        [
            InlineKeyboardButton(text=FAQ_DATA["faq_kyc"][f"title_{lang}"], callback_data="faq_kyc"),
            InlineKeyboardButton(text=FAQ_DATA["faq_security"][f"title_{lang}"], callback_data="faq_security"),
        ],
        [
            InlineKeyboardButton(text=contact, callback_data="start_direct_ticket"),
        ],
    ])
    await message.answer(t["support_title"], reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data.startswith("faq_"), StateFilter("*"))
async def cb_faq_details(callback: CallbackQuery):
    user_data = await get_user_data(callback.from_user.id)
    lang = user_data[0]
    faq_key = callback.data
    
    if faq_key in FAQ_DATA:
        ans = FAQ_DATA[faq_key][f"ans_{lang}"]
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=("بازگشت به سوالات" if lang=="fa" else "Back to FAQ"), callback_data="back_to_support")]])
        await callback.message.edit_text(ans, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "back_to_support", StateFilter("*"))
async def cb_back_to_support(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await support_menu_handler(callback.message, state)

@router.callback_query(F.data == "start_direct_ticket", StateFilter("*"))
async def cb_start_direct_ticket(callback: CallbackQuery, state: FSMContext):
    user_data = await get_user_data(callback.from_user.id)
    lang = user_data[0]
    await callback.message.answer(TEXTS[lang]["support_ask"], reply_markup=get_cancel_kb(lang))
    await state.set_state(UserStates.waiting_for_support_msg)

@router.message(StateFilter(UserStates.waiting_for_support_msg))
async def process_support_msg(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    t = TEXTS[lang]
    
    if message.text in [TEXTS["fa"]["btn_cancel"], TEXTS["en"]["btn_cancel"]]:
        await state.clear()
        return await message.answer(t["cancelled"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, user_id == settings.admin_id, user_id=user_id))
        
    async with aiosqlite.connect(settings.db_name) as db:
        c = await db.execute("INSERT INTO support_tickets (user_id, message) VALUES (?, ?)", (user_id, message.text))
        await db.commit()
        t_id = c.lastrowid
        
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 پاسخ به تیکت", callback_data=f"reply_ticket_{user_id}_{t_id}")]])
    try:
        await message.bot.send_message(settings.admin_id, f"📩 **تیکت پشتیبانی جدید (#{t_id})**\nکاربر: `{user_id}`\n\nمتن: {message.text}", reply_markup=admin_kb)
    except Exception:
        pass
        
    await message.answer(t["support_sent"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, user_id == settings.admin_id, user_id=user_id))
    await state.clear()

@router.callback_query(F.data.startswith("reply_ticket_"), StateFilter("*"))
async def cb_reply_ticket(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != settings.admin_id: return
    _, _, target_uid, t_id = callback.data.split("_")
    await state.update_data(reply_target_uid=int(target_uid), reply_ticket_id=int(t_id))
    await callback.message.answer(f"✍️ پاسخ برای تیکت کاربر `{target_uid}` را بنویسید:")
    await state.set_state(UserStates.waiting_for_admin_reply_ticket)

@router.message(StateFilter(UserStates.waiting_for_admin_reply_ticket))
async def process_admin_reply(message: Message, state: FSMContext):
    if message.from_user.id != settings.admin_id: return
    data = await state.get_data()
    target_uid = data.get("reply_target_uid")
    try:
        await message.bot.send_message(target_uid, f"🎧 **پاسخ پشتیبانی صرافی CX:**\n\n{message.text}")
        await message.answer("✅ پاسخ تیکت برای کاربر ارسال گردید.")
    except Exception:
        await message.answer("خطا در ارسال پیام به کاربر.")
    await state.clear()

async def security_center_handler(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, pin, wl = user_data[0], user_data[6], user_data[8]
    
    pin_status = "فعال ✅" if pin else "ثبت نشده ❌"
    wl_status = f"`{wl[:6]}...{wl[-4:]}`" if wl else ("ثبت نشده ❌" if lang == "fa" else "Not set ❌")
    
    text = (
        f"🔐 **{'مرکز امنیت حساب کاربری' if lang == 'fa' else 'Account Security Hub'}**\n\n"
        f"🔢 Security PIN: **{pin_status}**\n"
        f"🛡 Whitelisted Wallet: **{wl_status}**\n"
        f"⏳ Withdrawal Lock: 24h on modifications"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 تنظیم / تغییر PIN", callback_data="setup_sec_pin")],
        [InlineKeyboardButton(text="📍 ثبت ولت در لیست سفید", callback_data="setup_whitelist")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "setup_sec_pin", StateFilter("*"))
async def cb_setup_pin(callback: CallbackQuery, state: FSMContext):
    user_data = await get_user_data(callback.from_user.id)
    lang = user_data[0]
    await callback.message.answer(TEXTS[lang]["ask_pin_setup"], reply_markup=get_cancel_kb(lang))
    await state.set_state(UserStates.waiting_for_pin_setup)

@router.message(StateFilter(UserStates.waiting_for_pin_setup))
async def process_pin_setup(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    t = TEXTS[lang]
    
    if message.text in [TEXTS["fa"]["btn_cancel"], TEXTS["en"]["btn_cancel"]]:
        await state.clear()
        return await message.answer(t["cancelled"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, user_id == settings.admin_id, user_id=user_id))
        
    pin = message.text.strip()
    if not (pin.isdigit() and len(pin) == 4):
        return await message.answer("پین باید ۴ رقمی باشد.")
        
    cooldown = datetime.now() + timedelta(hours=24)
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE users SET security_pin = ?, cooldown_until = ? WHERE user_id = ?", (pin, str(cooldown), user_id))
        await db.commit()
        
    await log_security_event(user_id, "Security PIN Changed")
    await message.answer(t["pin_saved"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, user_id == settings.admin_id, user_id=user_id))
    await state.clear()

@router.callback_query(F.data == "setup_whitelist", StateFilter("*"))
async def cb_setup_whitelist(callback: CallbackQuery, state: FSMContext):
    user_data = await get_user_data(callback.from_user.id)
    lang = user_data[0]
    await callback.message.answer(TEXTS[lang]["whitelist_ask"], reply_markup=get_cancel_kb(lang))
    await state.set_state(UserStates.waiting_for_whitelist_addr)

@router.message(StateFilter(UserStates.waiting_for_whitelist_addr))
async def process_whitelist(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    t = TEXTS[lang]
    
    if message.text in [TEXTS["fa"]["btn_cancel"], TEXTS["en"]["btn_cancel"]]:
        await state.clear()
        return await message.answer(t["cancelled"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, user_id == settings.admin_id, user_id=user_id))
        
    addr = message.text.strip()
    cooldown = datetime.now() + timedelta(hours=24)
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE users SET whitelist_address = ?, cooldown_until = ? WHERE user_id = ?", (addr, str(cooldown), user_id))
        await db.commit()
        
    await log_security_event(user_id, "Whitelist Address Updated")
    await message.answer(t["whitelist_saved"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, user_id == settings.admin_id, user_id=user_id))
    await state.clear()

async def p2p_transfer_handler(message: Message, state: FSMContext):
    await state.clear()
    user_data = await get_user_data(message.from_user.id)
    lang, kyc = user_data[0], user_data[1]
    t = TEXTS[lang]
    if kyc < 2: return await message.answer(t["limit_feature"])
    await message.answer(t["transfer_desc"], reply_markup=get_cancel_kb(lang), parse_mode="Markdown")
    await state.set_state(UserStates.waiting_for_transfer_userid)

@router.message(StateFilter(UserStates.waiting_for_transfer_userid))
async def process_transfer_userid(message: Message, state: FSMContext):
    user_data = await get_user_data(message.from_user.id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    t = TEXTS[lang]
    if message.text in [TEXTS["fa"]["btn_cancel"], TEXTS["en"]["btn_cancel"]]:
        await state.clear()
        return await message.answer(t["cancelled"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, message.from_user.id == settings.admin_id, user_id=message.from_user.id))
    try:
        target_id = int(message.text.strip())
        if target_id == message.from_user.id: return await message.answer(t["transfer_err_self"])
        async with aiosqlite.connect(settings.db_name) as db:
            c = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (target_id,))
            if not await c.fetchone(): return await message.answer(t["transfer_err_notfound"])
        await state.update_data(transfer_target=target_id)
        await message.answer(t["transfer_amt_ask"], reply_markup=get_cancel_kb(lang))
        await state.set_state(UserStates.waiting_for_transfer_amount)
    except ValueError:
        await message.answer("شناسه نامعتبر است.")

@router.message(StateFilter(UserStates.waiting_for_transfer_amount))
async def process_transfer_amount(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    t = TEXTS[lang]
    if message.text in [TEXTS["fa"]["btn_cancel"], TEXTS["en"]["btn_cancel"]]:
        await state.clear()
        return await message.answer(t["cancelled"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, user_id == settings.admin_id, user_id=user_id))
    try:
        amount = float(message.text)
        if amount <= 0: return await message.answer(t["stake_invalid"])
        data = await state.get_data()
        target_id = data.get("transfer_target")
        try:
            sender_res, _ = await transfer_p2p(user_id, int(target_id), amount)
        except InsufficientBalance:
            return await message.answer(t["insufficient_bal"])
        except Exception as e:
            return await message.answer(f"{e}")
        await log_security_event(user_id, f"P2P {amount} -> {target_id}")
        await message.answer(
            f"✅ انتقال `{amount:g} TON` به کاربر `{target_id}` انجام شد.",
            reply_markup=get_main_dashboard_kb(lang, sender_res.ton_balance, kyc, user_id == settings.admin_id, user_id=user_id),
            parse_mode="Markdown",
        )
        try:
            await message.bot.send_message(
                int(target_id),
                f"💸 مبلغ `{amount:g} TON` از کاربر `{user_id}` دریافت کردید.",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        await state.clear()
    except ValueError:
        await message.answer(t["stake_invalid"])

@router.message(StateFilter(UserStates.waiting_for_withdraw_address))
async def process_with_addr(message: Message, state: FSMContext):
    user_data = await get_user_data(message.from_user.id)
    lang, wl, balance, kyc = user_data[0], user_data[8], user_data[4], user_data[1]
    t = TEXTS[lang]
    
    if message.text in [TEXTS["fa"]["btn_cancel"], TEXTS["en"]["btn_cancel"]]:
        await state.clear()
        return await message.answer(t["cancelled"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, message.from_user.id == settings.admin_id, user_id=message.from_user.id))
        
    addr = message.text.strip()
    if wl and addr != wl:
        return await message.answer(f"🔒 برداشت تنها به آدرس لیست سفید مجاز است:\n`{wl}`")
        
    await state.update_data(with_addr=addr)
    await message.answer(t["with_amt_ask"], reply_markup=get_cancel_kb(lang))
    await state.set_state(UserStates.waiting_for_withdraw_amount)

@router.message(StateFilter(UserStates.waiting_for_withdraw_amount))
async def process_with_amount(message: Message, state: FSMContext):
    user_data = await get_user_data(message.from_user.id)
    lang, balance, kyc = user_data[0], user_data[4], user_data[1]
    t = TEXTS[lang]
    
    if message.text in [TEXTS["fa"]["btn_cancel"], TEXTS["en"]["btn_cancel"]]:
        await state.clear()
        return await message.answer(t["cancelled"], reply_markup=get_main_dashboard_kb(lang, balance, kyc, message.from_user.id == settings.admin_id, user_id=message.from_user.id))
        
    try:
        amount = float(message.text)
        from bot.services.withdraw import check_withdraw_amount, get_withdraw_used_today
        used = await get_withdraw_used_today(message.from_user.id)
        lim = check_withdraw_amount(amount, int(kyc or 0), used_today=used)
        if not lim.get("ok"):
            err = lim.get("error")
            if err == "kyc_required":
                return await message.answer(
                    "KYC required for withdraw. Complete phone verification at least (L1)."
                    if lang == "en"
                    else "برای برداشت حداقل تأیید تلفن (سطح ۱) لازم است."
                )
            if err == "below_min":
                return await message.answer(
                    ("Minimum withdraw: %s TON" % lim["min"])
                    if lang == "en"
                    else ("حداقل برداشت: %s TON" % lim["min"])
                )
            if err == "above_kyc_max":
                return await message.answer(
                    ("Max for your KYC L%s: %s TON" % (int(lim["kyc_level"]), lim["max"]))
                    if lang == "en"
                    else ("سقف سطح KYC شما (L%s): %s TON" % (int(lim["kyc_level"]), lim["max"]))
                )
            if err == "daily_limit":
                return await message.answer(
                    ("Daily limit reached. Used %s / %s TON today." % (lim.get("used_today"), lim.get("daily_max")))
                    if lang == "en"
                    else ("سقف روزانه پر شده. امروز %s از %s TON." % (lim.get("used_today"), lim.get("daily_max")))
                )
            return await message.answer(str(err))
        if balance < amount: return await message.answer(t["insufficient_bal"])
        
        await state.update_data(with_amount=amount)
        await message.answer(t["ask_pin_verify"], reply_markup=get_cancel_kb(lang))
        await state.set_state(UserStates.waiting_for_withdraw_pin)
    except ValueError:
        return await message.answer(t["stake_invalid"])

@router.message(StateFilter(UserStates.waiting_for_withdraw_pin))
async def process_with_pin(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    lang, pin, balance, kyc = user_data[0], user_data[6], user_data[4], user_data[1]
    t = TEXTS[lang]
    
    if message.text.strip() != str(pin):
        return await message.answer(t["wrong_pin"])
        
    data = await state.get_data()
    amount = data["with_amount"]
    addr = data["with_addr"]
    
    try:
        ledger_res, req_id = await hold_withdraw(user_id, amount, address=addr)
    except InsufficientBalance:
        return await message.answer(t["insufficient_bal"])
    except Exception as e:
        return await message.answer(str(e))
    new_balance = ledger_res.ton_balance

    from bot.services.withdraw import try_auto_withdraw
    auto = await try_auto_withdraw(req_id, amount, addr)
    if auto.get("auto"):
        txh = str(auto.get("tx_hash") or "")
        await log_security_event(user_id, "Auto withdraw completed: %s TON tx=%s" % (amount, txh))
        await message.answer(
            "%s\nTX: `%s`" % (t.get("req_submitted") or "Submitted", txh),
            reply_markup=get_main_dashboard_kb(lang, new_balance, kyc, user_id == settings.admin_id, user_id=user_id),
            parse_mode="Markdown",
        )
        try:
            await message.bot.send_message(
                settings.admin_id,
                "Auto-withdraw #%s\nUser `%s`\n`%g TON`\nTX `%s`" % (req_id, user_id, amount, txh),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        await state.clear()
        return

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Approve settle", callback_data="adm_settle_%s" % req_id),
        InlineKeyboardButton(text="Reject", callback_data="adm_rejsettle_%s" % req_id),
    ]])
    await message.bot.send_message(
        settings.admin_id,
        "Withdraw #%s\nUser `%s`\n`%g TON`\n`%s`\npending: %s" % (
            req_id, user_id, amount, addr, auto.get("reason"),
        ),
        reply_markup=admin_kb,
        parse_mode="Markdown",
    )
    await log_security_event(user_id, "Withdrawal Requested: %s TON" % amount)
    await message.answer(
        t["req_submitted"],
        reply_markup=get_main_dashboard_kb(lang, new_balance, kyc, user_id == settings.admin_id, user_id=user_id),
    )
    await state.clear()


@router.callback_query(F.data.startswith("adm_settle_"), StateFilter("*"))
async def cb_admin_settle(callback: CallbackQuery):
    if callback.from_user.id != settings.admin_id:
        return
    req_id = int(callback.data.split("_")[2])
    try:
        async with aiosqlite.connect(settings.db_name) as db:
            db.row_factory = aiosqlite.Row
            c = await db.execute(
                "SELECT user_id, amount, address, status FROM requests WHERE request_id = ?",
                (req_id,),
            )
            req = await c.fetchone()
        if not req:
            return await callback.answer("request not found", show_alert=True)
        if req["status"] != "pending":
            return await callback.answer(f"status={req['status']}", show_alert=True)

        tx_hash = None
        if getattr(settings, "withdraw_onchain_enabled", True):
            from bot.services.ton_chain import (
                TonSendError,
                TonWalletNotConfigured,
                hot_wallet_configured,
                send_ton,
            )
            if not hot_wallet_configured():
                return await callback.answer(
                    "HOT_WALLET_MNEMONIC تنظیم نشده — ارسال زنجیره‌ای ممکن نیست",
                    show_alert=True,
                )
            try:
                await callback.answer("در حال ارسال روی شبکه TON…")
                tx_hash = await send_ton(
                    str(req["address"]),
                    float(req["amount"]),
                    comment=f"cx_wd_{req_id}",
                )
            except TonWalletNotConfigured as e:
                return await callback.answer(str(e), show_alert=True)
            except TonSendError as e:
                return await callback.answer(f"ارسال ناموفق: {e}", show_alert=True)
        else:
            tx_hash = f"manual_{req_id}"

        await complete_withdraw(req_id, tx_hash=tx_hash)
    except BusinessRuleError as e:
        return await callback.answer(str(e), show_alert=True)
    await callback.message.edit_text(f"{callback.message.text}\n\n✅ **تسویه تایید شد.**")
    try:
        async with aiosqlite.connect(settings.db_name) as db:
            c = await db.execute(
                "SELECT user_id, amount, tx_hash FROM requests WHERE request_id = ?",
                (req_id,),
            )
            row = await c.fetchone()
        if row:
            await callback.bot.send_message(
                row[0],
                f"🔔 **تراکنش با موفقیت تسویه شد!**\nمبلغ: `{row[1]:g} TON`\nTXID:\n`{row[2]}`",
                parse_mode="Markdown",
            )
    except Exception:
        pass




@router.callback_query(F.data.in_({"prop_10k", "prop_50k"}), StateFilter("*"))
async def cb_prop_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    user_data = await get_user_data(user_id)
    lang, kyc = user_data[0], user_data[1]
    texts = TEXTS.get(lang, TEXTS["fa"])
    plan = 10000.0 if callback.data == "prop_10k" else 50000.0
    fee = 50.0 if callback.data == "prop_10k" else 200.0
    try:
        from bot.db.ledger import start_prop_challenge
        result, size = await start_prop_challenge(user_id, plan_size=plan, fee=fee)
    except InsufficientBalance:
        return await callback.answer(texts["insufficient_bal"], show_alert=True)
    except Exception as e:
        return await callback.answer(str(e), show_alert=True)
    await callback.message.answer(
        "%s\nPlan: `%g`\nBalance: `%g TON`" % (
            texts.get("prop_success") or "Prop challenge activated.",
            size,
            result.ton_balance,
        ),
        parse_mode="Markdown",
        reply_markup=get_main_dashboard_kb(lang, result.ton_balance, kyc, user_id == settings.admin_id, user_id=user_id),
    )
    await callback.answer("OK")
