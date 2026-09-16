"""CX Telegram Admin Hub — bilingual FA/EN, ADMIN_ID only."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import aiosqlite
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import settings
from bot.db import get_user_data
from bot.db.ledger import (
    TxKind,
    InsufficientBalance,
    complete_withdraw,
    credit_ton,
    debit_ton,
    reject_withdraw,
)
from bot.states import UserStates

logger = logging.getLogger("cx.admin")
router = Router(name="admin")

# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------
T = {
    "fa": {
        "denied": "دسترسی ندارید",
        "title": "مرکز مدیریت CX",
        "users": "کاربران",
        "ledger_ton": "مجموع TON",
        "ledger_usdt": "مجموع USDT",
        "pending_wd": "برداشت در انتظار",
        "open_binary": "باینری باز",
        "active_prop": "پراپ فعال",
        "loans": "وام‌ها",
        "freeze": "فریز اضطراری",
        "maint": "تعمیرات",
        "on": "روشن",
        "off": "خاموش",
        "today_users": "کاربر جدید امروز",
        "today_tx": "تراکنش امروز",
        "today_volume": "حجم برداشت/واریز امروز",
        "open_tickets": "تیکت باز",
        "btn_refresh": "بروزرسانی",
        "btn_freeze": "فریز",
        "btn_maint": "تعمیرات",
        "btn_wd": "برداشت‌ها",
        "btn_binary": "باینری باز",
        "btn_lookup": "جستجوی کاربر",
        "btn_credit": "افزایش موجودی",
        "btn_debit": "کاهش موجودی",
        "btn_broadcast": "پیام همگانی",
        "btn_prop": "نمای پراپ",
        "btn_tx": "تراکنش‌ها",
        "btn_tickets": "تیکت‌ها",
        "btn_kyc": "KYC در انتظار",
        "btn_daily": "آمار روزانه",
        "btn_lang": "English",
        "no_wd": "برداشت معلقی نیست",
        "no_binary": "معامله باینری باز نیست",
        "no_prop": "حساب پراپ نیست",
        "no_tx": "تراکنشی نیست",
        "no_tickets": "تیکت بازی نیست",
        "no_kyc": "درخواست KYC معلقی در صف نیست (از عکس ارسالی کاربر بررسی کنید)",
        "lookup_ask": "آیدی عددی کاربر را بفرستید:",
        "credit_ask": "فرمت:\n`user_id amount`\nیا فقط `amount` برای خودتان",
        "debit_ask": "فرمت:\n`user_id amount`",
        "broadcast_ask": "متن پیام همگانی را بفرستید (یا /cancel):",
        "cancelled": "لغو شد",
        "invalid_id": "آیدی نامعتبر",
        "user_not_found": "کاربر پیدا نشد",
        "amount_pos": "مبلغ باید مثبت باشد",
        "credited": "افزایش موجودی انجام شد",
        "debited": "کاهش موجودی انجام شد",
        "insufficient": "موجودی کاربر کافی نیست",
        "approved": "تأیید شد",
        "rejected": "رد و بازگردانی شد",
        "broadcast_run": "در حال ارسال…",
        "broadcast_done": "ارسال تمام شد",
        "updated": "بروز شد",
        "wd_title": "برداشت",
        "user": "کاربر",
        "amount": "مبلغ",
        "address": "آدرس",
        "at": "زمان",
        "approve": "تأیید",
        "reject": "رد + بازگشت وجه",
        "close_ticket": "بستن تیکت",
        "ticket": "تیکت",
        "reply_hint": "برای پاسخ از دکمه پاسخ تیکت در پیام اصلی استفاده کنید",
    },
    "en": {
        "denied": "Access denied",
        "title": "CX Admin Hub",
        "users": "Users",
        "ledger_ton": "Ledger TON",
        "ledger_usdt": "Ledger USDT",
        "pending_wd": "Pending withdraws",
        "open_binary": "Open binary",
        "active_prop": "Active prop",
        "loans": "Loans",
        "freeze": "Emergency freeze",
        "maint": "Maintenance",
        "on": "ON",
        "off": "OFF",
        "today_users": "New users today",
        "today_tx": "Tx today",
        "today_volume": "Deposit/withdraw volume today",
        "open_tickets": "Open tickets",
        "btn_refresh": "Refresh",
        "btn_freeze": "Freeze",
        "btn_maint": "Maintenance",
        "btn_wd": "Withdrawals",
        "btn_binary": "Open binary",
        "btn_lookup": "Lookup user",
        "btn_credit": "Credit TON",
        "btn_debit": "Debit TON",
        "btn_broadcast": "Broadcast",
        "btn_prop": "Prop overview",
        "btn_tx": "Recent txs",
        "btn_tickets": "Tickets",
        "btn_kyc": "KYC queue",
        "btn_daily": "Daily stats",
        "btn_lang": "فارسی",
        "no_wd": "No pending withdrawals",
        "no_binary": "No open binary trades",
        "no_prop": "No prop accounts",
        "no_tx": "No transactions",
        "no_tickets": "No open tickets",
        "no_kyc": "No queued KYC items here (review user-sent photos)",
        "lookup_ask": "Send numeric user id:",
        "credit_ask": "Format:\n`user_id amount`\nor just `amount` for yourself",
        "debit_ask": "Format:\n`user_id amount`",
        "broadcast_ask": "Send broadcast text (or /cancel):",
        "cancelled": "Cancelled",
        "invalid_id": "Invalid user id",
        "user_not_found": "User not found",
        "amount_pos": "Amount must be positive",
        "credited": "Credit applied",
        "debited": "Debit applied",
        "insufficient": "Insufficient user balance",
        "approved": "Approved",
        "rejected": "Rejected + refunded",
        "broadcast_run": "Broadcasting…",
        "broadcast_done": "Broadcast finished",
        "updated": "Updated",
        "wd_title": "Withdraw",
        "user": "User",
        "amount": "Amount",
        "address": "Address",
        "at": "At",
        "approve": "Approve",
        "reject": "Reject + refund",
        "close_ticket": "Close ticket",
        "ticket": "Ticket",
        "reply_hint": "Use reply-ticket button from the original ticket message",
    },
}

_admin_lang: dict[int, str] = {}


def _lang(uid: int) -> str:
    return _admin_lang.get(int(uid), "fa")


def _t(uid: int) -> dict:
    return T.get(_lang(uid), T["fa"])


def _admin_only(user_id: int) -> bool:
    return int(user_id) == int(settings.admin_id)


def admin_kb(uid: int) -> InlineKeyboardMarkup:
    t = _t(uid)
    freeze = t["on"] if settings.emergency_freeze else t["off"]
    maint = t["on"] if getattr(settings, "maintenance_mode", False) else t["off"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_refresh"], callback_data="adm_refresh")],
            [
                InlineKeyboardButton(text=f"{t['btn_freeze']}: {freeze}", callback_data="adm_toggle_freeze"),
                InlineKeyboardButton(text=f"{t['btn_maint']}: {maint}", callback_data="adm_toggle_maint"),
            ],
            [
                InlineKeyboardButton(text=t["btn_wd"], callback_data="adm_withdraws"),
                InlineKeyboardButton(text=t["btn_tickets"], callback_data="adm_tickets"),
            ],
            [
                InlineKeyboardButton(text=t["btn_binary"], callback_data="adm_open_binary"),
                InlineKeyboardButton(text=t["btn_prop"], callback_data="adm_prop"),
            ],
            [
                InlineKeyboardButton(text=t["btn_lookup"], callback_data="adm_lookup"),
                InlineKeyboardButton(text=t["btn_daily"], callback_data="adm_daily"),
            ],
            [
                InlineKeyboardButton(text=t["btn_credit"], callback_data="adm_credit"),
                InlineKeyboardButton(text=t["btn_debit"], callback_data="adm_debit"),
            ],
            [
                InlineKeyboardButton(text=t["btn_tx"], callback_data="adm_recent_tx"),
                InlineKeyboardButton(text=t["btn_kyc"], callback_data="adm_kyc"),
            ],
            [
                InlineKeyboardButton(text=t["btn_broadcast"], callback_data="adm_broadcast"),
                InlineKeyboardButton(text=t["btn_lang"], callback_data="adm_lang"),
            ],
        ]
    )


async def _stats_text(uid: int) -> str:
    t = _t(uid)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        total_users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        bal = await (await db.execute(
            "SELECT COALESCE(SUM(balance),0), COALESCE(SUM(usdt_balance),0) FROM users"
        )).fetchone()
        ton_sum, usdt_sum = float(bal[0] or 0), float(bal[1] or 0)
        pending_wd = await (await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM requests WHERE req_type='withdraw' AND status='pending'"
        )).fetchone()
        open_bin = (await (await db.execute(
            "SELECT COUNT(*) FROM binary_trades WHERE status='open'"
        )).fetchone())[0]
        try:
            prop_active = (await (await db.execute(
                "SELECT COUNT(*) FROM prop_accounts WHERE status='active'"
            )).fetchone())[0]
        except Exception:
            prop_active = 0
        loans = (await (await db.execute(
            "SELECT COALESCE(SUM(loan_amount),0) FROM users WHERE loan_amount > 0"
        )).fetchone())[0]
        try:
            open_tickets = (await (await db.execute(
                "SELECT COUNT(*) FROM support_tickets WHERE status='open'"
            )).fetchone())[0]
        except Exception:
            open_tickets = 0
        # today
        today = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            new_u = (await (await db.execute(
                "SELECT COUNT(*) FROM users WHERE date(join_date)=date(?)", (today,)
            )).fetchone())[0]
        except Exception:
            new_u = 0
        try:
            tx_today = (await (await db.execute(
                "SELECT COUNT(*) FROM transactions WHERE date(created_at)=date(?)", (today,)
            )).fetchone())[0]
        except Exception:
            tx_today = 0

    return (
        f"**{t['title']}**\n\n"
        f"{t['users']}: `{total_users}`\n"
        f"{t['ledger_ton']}: `{ton_sum:,.2f}`\n"
        f"{t['ledger_usdt']}: `{usdt_sum:,.2f}`\n"
        f"{t['pending_wd']}: `{pending_wd[0]}` (`{float(pending_wd[1] or 0):,.2f}` TON)\n"
        f"{t['open_binary']}: `{open_bin}`\n"
        f"{t['active_prop']}: `{prop_active}`\n"
        f"{t['open_tickets']}: `{open_tickets}`\n"
        f"{t['loans']}: `{float(loans or 0):,.2f}` TON\n"
        f"{t['today_users']}: `{new_u}`\n"
        f"{t['today_tx']}: `{tx_today}`\n"
        f"{t['freeze']}: `{t['on'] if settings.emergency_freeze else t['off']}`\n"
        f"{t['maint']}: `{t['on'] if getattr(settings, 'maintenance_mode', False) else t['off']}`"
    )


@router.message(F.text.in_({"پنل مدیریت", "Admin Hub", "/admin"}), StateFilter("*"))
async def admin_dashboard(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    await state.clear()
    uid = message.from_user.id
    await message.answer(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")


@router.callback_query(F.data == "adm_lang", StateFilter("*"))
async def cb_lang(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    uid = callback.from_user.id
    _admin_lang[uid] = "en" if _lang(uid) == "fa" else "fa"
    try:
        await callback.message.edit_text(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")
    except Exception:
        await callback.message.answer(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")
    await callback.answer(_t(uid)["updated"])


@router.callback_query(F.data == "adm_refresh", StateFilter("*"))
async def cb_refresh(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    await state.clear()
    uid = callback.from_user.id
    try:
        await callback.message.edit_text(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")
    except Exception:
        await callback.message.answer(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")
    await callback.answer(_t(uid)["updated"])


@router.callback_query(F.data == "adm_toggle_freeze", StateFilter("*"))
async def cb_freeze(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    settings.emergency_freeze = not bool(settings.emergency_freeze)
    uid = callback.from_user.id
    await callback.answer(f"{_t(uid)['freeze']}: {settings.emergency_freeze}", show_alert=True)
    try:
        await callback.message.edit_text(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")
    except Exception:
        pass


@router.callback_query(F.data == "adm_toggle_maint", StateFilter("*"))
async def cb_maint(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    settings.maintenance_mode = not bool(getattr(settings, "maintenance_mode", False))
    uid = callback.from_user.id
    await callback.answer(f"{_t(uid)['maint']}: {settings.maintenance_mode}", show_alert=True)
    try:
        await callback.message.edit_text(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")
    except Exception:
        pass


@router.callback_query(F.data == "adm_daily", StateFilter("*"))
async def cb_daily(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    uid = callback.from_user.id
    t = _t(uid)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        new_u = (await (await db.execute(
            "SELECT COUNT(*) FROM users WHERE date(join_date)=date(?)", (today,)
        )).fetchone())[0]
        tx_c = (await (await db.execute(
            "SELECT COUNT(*) FROM transactions WHERE date(created_at)=date(?)", (today,)
        )).fetchone())[0]
        dep = (await (await db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE kind='deposit' AND date(created_at)=date(?)",
            (today,),
        )).fetchone())[0]
        wd = (await (await db.execute(
            "SELECT COALESCE(SUM(ABS(amount)),0) FROM transactions WHERE kind LIKE 'withdraw%' AND date(created_at)=date(?)",
            (today,),
        )).fetchone())[0]
        bin_open = (await (await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM binary_trades WHERE date(open_time)=date(?)",
            (today,),
        )).fetchone())
        prop_fee = (await (await db.execute(
            "SELECT COALESCE(SUM(ABS(amount)),0) FROM transactions WHERE kind='prop_fee' AND date(created_at)=date(?)",
            (today,),
        )).fetchone())[0]
    text = (
        f"**{t['btn_daily']}** (`{today}` UTC)\n\n"
        f"{t['today_users']}: `{new_u}`\n"
        f"{t['today_tx']}: `{tx_c}`\n"
        f"Deposits: `{float(dep or 0):.4f}` TON\n"
        f"Withdraw-related: `{float(wd or 0):.4f}` TON\n"
        f"Binary opened: `{bin_open[0]}` / volume `{float(bin_open[1] or 0):.2f}`\n"
        f"Prop fees: `{float(prop_fee or 0):.2f}` TON"
    )
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "adm_withdraws", StateFilter("*"))
async def cb_withdraws(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    uid = callback.from_user.id
    t = _t(uid)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT request_id, user_id, amount, address, created_at
            FROM requests
            WHERE req_type='withdraw' AND status='pending'
            ORDER BY request_id ASC LIMIT 10
            """
        )).fetchall()
    if not rows:
        return await callback.answer(t["no_wd"], show_alert=True)
    for r in rows:
        rid = r["request_id"]
        text = (
            f"{t['wd_title']} `#{rid}`\n"
            f"{t['user']}: `{r['user_id']}`\n"
            f"{t['amount']}: `{float(r['amount']):.4f}` TON\n"
            f"{t['address']}:\n`{r['address']}`\n"
            f"{t['at']}: `{r['created_at']}`"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text=t["approve"], callback_data=f"adm_wd_ok_{rid}"),
                InlineKeyboardButton(text=t["reject"], callback_data=f"adm_wd_no_{rid}"),
            ]]
        )
        await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("adm_wd_ok_"), StateFilter("*"))
async def cb_wd_ok(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    rid = int(callback.data.split("_")[-1])
    t = _t(callback.from_user.id)
    try:
        await complete_withdraw(rid, tx_hash=f"admin_{rid}")
        await callback.message.edit_text((callback.message.text or "") + f"\n\n{_t(callback.from_user.id)['approved']}")
        await callback.answer(t["approved"])
    except Exception as e:
        await callback.answer(str(e), show_alert=True)


@router.callback_query(F.data.startswith("adm_wd_no_"), StateFilter("*"))
async def cb_wd_no(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    rid = int(callback.data.split("_")[-1])
    t = _t(callback.from_user.id)
    try:
        res = await reject_withdraw(rid)
        await callback.message.edit_text(
            (callback.message.text or "") + f"\n\n{t['rejected']} · bal `{res.ton_balance:.4f}`"
        )
        await callback.answer(t["rejected"])
    except Exception as e:
        await callback.answer(str(e), show_alert=True)


@router.callback_query(F.data == "adm_tickets", StateFilter("*"))
async def cb_tickets(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    uid = callback.from_user.id
    t = _t(uid)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT ticket_id, user_id, message, status, created_at
            FROM support_tickets WHERE status='open'
            ORDER BY ticket_id DESC LIMIT 15
            """
        )).fetchall()
    if not rows:
        return await callback.answer(t["no_tickets"], show_alert=True)
    for r in rows:
        tid = r["ticket_id"]
        msg = (r["message"] or "")[:500]
        text = (
            f"{t['ticket']} `#{tid}`\n"
            f"{t['user']}: `{r['user_id']}`\n"
            f"{t['at']}: `{r['created_at']}`\n\n{msg}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Reply",
                        callback_data=f"reply_ticket_{r['user_id']}_{tid}",
                    ),
                    InlineKeyboardButton(
                        text=t["close_ticket"],
                        callback_data=f"adm_ticket_close_{tid}",
                    ),
                ]
            ]
        )
        await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("adm_ticket_close_"), StateFilter("*"))
async def cb_ticket_close(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    tid = int(callback.data.split("_")[-1])
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE support_tickets SET status='closed' WHERE ticket_id=?", (tid,))
        await db.commit()
    await callback.message.edit_text((callback.message.text or "") + "\n\nClosed.")
    await callback.answer("OK")


@router.callback_query(F.data == "adm_open_binary", StateFilter("*"))
async def cb_open_binary(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    t = _t(callback.from_user.id)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, user_id, symbol, direction, amount, entry_price, expire_time
            FROM binary_trades WHERE status='open' ORDER BY id DESC LIMIT 15
            """
        )).fetchall()
    if not rows:
        return await callback.answer(t["no_binary"], show_alert=True)
    lines = [f"**{t['btn_binary']}**\n"]
    for r in rows:
        lines.append(
            f"#{r['id']} u`{r['user_id']}` {r['direction']} {r['amount']} {r['symbol']} "
            f"@ {r['entry_price']} exp `{r['expire_time']}`"
        )
    await callback.message.answer("\n".join(lines), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "adm_prop", StateFilter("*"))
async def cb_prop(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    t = _t(callback.from_user.id)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        try:
            rows = await (await db.execute(
                """
                SELECT user_id, plan_size, virtual_balance, status, trades_count
                FROM prop_accounts ORDER BY rowid DESC LIMIT 15
                """
            )).fetchall()
        except Exception:
            rows = []
    if not rows:
        return await callback.answer(t["no_prop"], show_alert=True)
    lines = [f"**{t['btn_prop']}**\n"]
    for r in rows:
        tc = r["trades_count"] if "trades_count" in r.keys() else "-"
        lines.append(
            f"u`{r['user_id']}` plan `{r['plan_size']}` virt `{float(r['virtual_balance'] or 0):.2f}` "
            f"`{r['status']}` trades `{tc}`"
        )
    await callback.message.answer("\n".join(lines), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "adm_recent_tx", StateFilter("*"))
async def cb_recent_tx(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    t = _t(callback.from_user.id)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, user_id, kind, amount, currency, created_at
            FROM transactions ORDER BY id DESC LIMIT 20
            """
        )).fetchall()
    if not rows:
        return await callback.answer(t["no_tx"], show_alert=True)
    lines = [f"**{t['btn_tx']}**\n"]
    for r in rows:
        lines.append(
            f"#{r['id']} u`{r['user_id']}` `{r['kind']}` `{r['amount']}` {r['currency']} `{r['created_at']}`"
        )
    await callback.message.answer("\n".join(lines), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "adm_kyc", StateFilter("*"))
async def cb_kyc(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    t = _t(callback.from_user.id)
    # Users still at level 1 (phone verified, docs may be pending via photo flow)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT user_id, phone, email, kyc_level, balance
            FROM users WHERE kyc_level = 1
            ORDER BY user_id DESC LIMIT 20
            """
        )).fetchall()
    if not rows:
        return await callback.answer(t["no_kyc"], show_alert=True)
    for r in rows:
        uid = r["user_id"]
        text = (
            f"KYC L1 user `{uid}`\n"
            f"Phone: `{r['phone'] or '-'}`\n"
            f"Email: `{r['email'] or '-'}`\n"
            f"Balance: `{float(r['balance'] or 0):.4f}` TON"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="Approve L2", callback_data=f"adm_kyc_accept_{uid}"),
                InlineKeyboardButton(text="Reject", callback_data=f"adm_kyc_reject_{uid}"),
            ]]
        )
        await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "adm_lookup", StateFilter("*"))
async def cb_lookup(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    await state.set_state(UserStates.waiting_for_admin_lookup)
    await callback.message.answer(_t(callback.from_user.id)["lookup_ask"])
    await callback.answer()


@router.message(StateFilter(UserStates.waiting_for_admin_lookup))
async def process_lookup(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    t = _t(message.from_user.id)
    try:
        uid = int(message.text.strip())
    except Exception:
        return await message.answer(t["invalid_id"])
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT user_id, balance, usdt_balance, kyc_level, loan_amount, lang, phone, email FROM users WHERE user_id=?",
            (uid,),
        )).fetchone()
    await state.clear()
    if not row:
        return await message.answer(t["user_not_found"])
    text = (
        f"**User `{uid}`**\n"
        f"TON: `{float(row['balance'] or 0):.4f}`\n"
        f"USDT: `{float(row['usdt_balance'] or 0):.4f}`\n"
        f"KYC: `{row['kyc_level']}`\n"
        f"Loan: `{float(row['loan_amount'] or 0):.4f}`\n"
        f"Lang: `{row['lang']}`\n"
        f"Phone: `{row['phone'] or '-'}`\n"
        f"Email: `{row['email'] or '-'}`"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=admin_kb(message.from_user.id))


@router.callback_query(F.data == "adm_credit", StateFilter("*"))
async def cb_credit(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    await state.set_state(UserStates.waiting_for_admin_add_balance)
    await callback.message.answer(_t(callback.from_user.id)["credit_ask"], parse_mode="Markdown")
    await callback.answer()


@router.message(StateFilter(UserStates.waiting_for_admin_add_balance))
async def process_credit(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    t = _t(message.from_user.id)
    try:
        parts = message.text.strip().split()
        if len(parts) >= 2:
            target_id = int(parts[0])
            amt = float(parts[1])
        else:
            target_id = message.from_user.id
            amt = float(parts[0])
        if amt <= 0:
            return await message.answer(t["amount_pos"])
        res = await credit_ton(
            target_id, amt, kind=TxKind.ADMIN_CREDIT, meta={"by_admin": message.from_user.id}
        )
        await state.clear()
        await message.answer(
            f"{t['credited']}\nUser `{target_id}` +`{amt:g}` TON\nBal `{res.ton_balance:g}`",
            parse_mode="Markdown",
            reply_markup=admin_kb(message.from_user.id),
        )
        try:
            await message.bot.send_message(target_id, f"Admin credit +{amt:g} TON")
        except Exception:
            pass
    except Exception as e:
        await message.answer(f"Error: {e}")


@router.callback_query(F.data == "adm_debit", StateFilter("*"))
async def cb_debit(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    await state.set_state(UserStates.waiting_for_admin_debit)
    await callback.message.answer(_t(callback.from_user.id)["debit_ask"], parse_mode="Markdown")
    await callback.answer()


@router.message(StateFilter(UserStates.waiting_for_admin_debit))
async def process_debit(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    t = _t(message.from_user.id)
    try:
        parts = message.text.strip().split()
        target_id = int(parts[0])
        amt = float(parts[1])
        if amt <= 0:
            return await message.answer(t["amount_pos"])
        res = await debit_ton(
            target_id, amt, kind=TxKind.ADMIN_DEBIT, meta={"by_admin": message.from_user.id}
        )
        await state.clear()
        await message.answer(
            f"{t['debited']}\nUser `{target_id}` -`{amt:g}` TON\nBal `{res.ton_balance:g}`",
            parse_mode="Markdown",
            reply_markup=admin_kb(message.from_user.id),
        )
    except InsufficientBalance:
        await message.answer(t["insufficient"])
    except Exception as e:
        await message.answer(f"Error: {e}")


@router.callback_query(F.data == "adm_broadcast", StateFilter("*"))
async def cb_broadcast(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    await state.set_state(UserStates.waiting_for_admin_broadcast)
    await callback.message.answer(_t(callback.from_user.id)["broadcast_ask"])
    await callback.answer()


@router.message(StateFilter(UserStates.waiting_for_admin_broadcast))
async def process_broadcast(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    t = _t(message.from_user.id)
    if (message.text or "").strip().lower() in ("/cancel", "cancel", "لغو"):
        await state.clear()
        return await message.answer(t["cancelled"], reply_markup=admin_kb(message.from_user.id))
    text = message.text or ""
    await state.clear()
    async with aiosqlite.connect(settings.db_name) as db:
        ids = [r[0] for r in await (await db.execute("SELECT user_id FROM users")).fetchall()]
    ok = fail = 0
    await message.answer(f"{t['broadcast_run']} ({len(ids)})")
    for uid in ids:
        try:
            await message.bot.send_message(uid, text)
            ok += 1
        except Exception:
            fail += 1
    await message.answer(
        f"{t['broadcast_done']}\nOK `{ok}` / fail `{fail}`",
        parse_mode="Markdown",
        reply_markup=admin_kb(message.from_user.id),
    )


# legacy
@router.callback_query(F.data == "toggle_emergency_freeze", StateFilter("*"))
async def legacy_freeze(callback: CallbackQuery):
    await cb_freeze(callback)


@router.callback_query(F.data == "admin_add_bal", StateFilter("*"))
async def legacy_add_bal(callback: CallbackQuery, state: FSMContext):
    await cb_credit(callback, state)
