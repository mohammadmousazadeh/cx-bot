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
from bot.keyboards.main import admin_webapp_url
from aiogram.types import WebAppInfo

logger = logging.getLogger("cx.admin")
router = Router(name="admin")

# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------
T = {
    "fa": {
        "denied": "دسترسی ندارید",
        "title": "مرکز کنترل CX",
        "users": "کاربران",
        "ledger_ton": "مجموع TON",
        "ledger_usdt": "مجموع USDT",
        "pending_wd": "برداشت در انتظار",
        "open_binary": "باینری باز",
        "active_prop": "پراپ فعال",
        "loans": "وام‌ها",
        "freeze": "فریز اضطراری",
        "maint": "تعمیرات",
        "on": "فعال",
        "off": "غیرفعال",
        "today_users": "کاربر جدید امروز",
        "today_tx": "تراکنش امروز",
        "today_volume": "حجم برداشت/واریز امروز",
        "open_tickets": "تیکت باز",
        "btn_refresh": "همگام‌سازی",
        "btn_freeze": "قفل اضطراری",
        "btn_maint": "حالت تعمیر",
        "btn_wd": "صف برداشت",
        "btn_binary": "باینری فعال",
        "btn_lookup": "پروفایل کاربر",
        "btn_credit": "واریز دستی",
        "btn_debit": "کسر دستی",
        "btn_broadcast": "اعلامیه سراسری",
        "btn_prop": "میز پراپ",
        "btn_tx": "دفترکل",
        "btn_tickets": "پشتیبانی",
        "btn_kyc": "میز احراز",
        "btn_daily": "گزارش روز",
        "btn_lang": "English",
        "sec_system": "▸ سیستم",
        "sec_ops": "▸ عملیات",
        "sec_users": "▸ کاربران",
        "sec_finance": "▸ خزانه",
        "sec_more": "— بیشتر —",
        "btn_home": "خانه ادمین",
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
        "title": "CX Control Center",
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
        "btn_refresh": "Sync",
        "btn_freeze": "Kill switch",
        "btn_maint": "Maintenance",
        "btn_wd": "Withdraw queue",
        "btn_binary": "Live binary",
        "btn_lookup": "User profile",
        "btn_credit": "Manual credit",
        "btn_debit": "Manual debit",
        "btn_broadcast": "Global notice",
        "btn_prop": "Prop desk",
        "btn_tx": "Ledger feed",
        "btn_tickets": "Support desk",
        "btn_kyc": "KYC desk",
        "btn_daily": "Daily report",
        "btn_lang": "فارسی",
        "sec_system": "▸ SYSTEM",
        "sec_ops": "▸ OPERATIONS",
        "sec_users": "▸ USERS",
        "sec_finance": "▸ TREASURY",
        "sec_more": "— More —",
        "btn_home": "Admin home",
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
_2fa_sessions: dict[int, float] = {}  # uid -> ok_until



def _toast(uid: int, fa: str, en: str) -> str:
    return fa if _lang(uid) == "fa" else en

def _lang(uid: int) -> str:
    return _admin_lang.get(int(uid), "fa")


def _t(uid: int) -> dict:
    return T.get(_lang(uid), T["fa"])


def _admin_only(user_id: int) -> bool:
    return int(user_id) == int(settings.admin_id)



def _admin_2fa_ok(code: str) -> bool:
    from bot.services.admin_2fa import is_2fa_enabled, verify_totp
    if not is_2fa_enabled():
        return True
    return verify_totp(code)


def _admin_2fa_session_ok(uid: int) -> bool:
    from bot.services.admin_2fa import is_2fa_enabled
    if not is_2fa_enabled():
        return True
    import time as _t
    return _2fa_sessions.get(int(uid), 0) > _t.time()


def _admin_2fa_session_grant(uid: int, sec: float = 300) -> None:
    import time as _t
    _2fa_sessions[int(uid)] = _t.time() + sec


async def _ask_or_check_2fa(callback_or_msg, state, next_action: str) -> bool:
    """Return True if 2FA already satisfied; else ask for code and store next_action."""
    from bot.services.admin_2fa import is_2fa_enabled
    from bot.config import settings as _s
    if not is_2fa_enabled():
        return True
    if not (getattr(_s, "admin_2fa_required", False) or is_2fa_enabled()):
        return True
    # Always require when secret configured
    data = await state.get_data()
    if data.get("admin_2fa_ok_until", 0) > __import__("time").time():
        return True
    await state.update_data(admin_2fa_next=next_action)
    uid = callback_or_msg.from_user.id
    fa = _lang(uid) == "fa"
    text = "کد ۶ رقمی Google Authenticator را بفرستید:" if fa else "Send your 6-digit Authenticator code:"
    if hasattr(callback_or_msg, "message"):
        await callback_or_msg.message.answer(text)
        await callback_or_msg.answer()
    else:
        await callback_or_msg.answer(text)
    from bot.states import UserStates
    # reuse a generic state if exists - or store in FSM with admin flag
    await state.set_state(UserStates.waiting_for_admin_2fa if hasattr(UserStates, "waiting_for_admin_2fa") else None)
    return False

def admin_kb(uid: int, menu: str = "home") -> InlineKeyboardMarkup:
    """Nested admin menus: home -> system/ops/users/treasury."""
    t = _t(uid)
    freeze = t["on"] if settings.emergency_freeze else t["off"]
    maint = t["on"] if getattr(settings, "maintenance_mode", False) else t["off"]
    fa = _lang(uid) == "fa"
    back = t.get("btn_back", "بازگشت" if fa else "Back")
    home = t.get("btn_home", "خانه ادمین" if fa else "Admin home")

    def row(*btns):
        return list(btns)

    if menu == "system":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=("کنسول وب" if fa else "Web console"), web_app=WebAppInfo(url=admin_webapp_url(uid)))],
            [
                InlineKeyboardButton(text=("بروزرسانی" if fa else "Refresh"), callback_data="adm_refresh"),
                InlineKeyboardButton(text=("English" if fa else "فارسی"), callback_data="adm_lang"),
            ],
            [InlineKeyboardButton(text=("قفل: " if fa else "Freeze: ") + freeze, callback_data="adm_toggle_freeze"),
             InlineKeyboardButton(text=("تعمیر: " if fa else "Maint: ") + maint, callback_data="adm_toggle_maint")],
            [InlineKeyboardButton(text=t.get("btn_metrics", "آمار زنده" if fa else "Live metrics"), callback_data="adm_metrics")],
            [InlineKeyboardButton(text=("باز کردن ۲FA" if fa else "Unlock 2FA"), callback_data="adm_2fa_unlock")],
            [InlineKeyboardButton(text=f"« {back}", callback_data="adm_menu_home")],
        ])

    if menu == "ops":
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=t["btn_wd"], callback_data="adm_withdraws"),
                InlineKeyboardButton(text=t["btn_tickets"], callback_data="adm_tickets"),
            ],
            [
                InlineKeyboardButton(text=t["btn_binary"], callback_data="adm_open_binary"),
                InlineKeyboardButton(text=t["btn_prop"], callback_data="adm_prop"),
            ],
            [
                InlineKeyboardButton(text=t["btn_daily"], callback_data="adm_daily"),
                InlineKeyboardButton(text=t["btn_tx"], callback_data="adm_recent_tx"),
            ],
            [InlineKeyboardButton(text=f"« {back}", callback_data="adm_menu_home")],
        ])

    if menu == "users":
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=t["btn_lookup"], callback_data="adm_lookup"),
                InlineKeyboardButton(text=t["btn_kyc"], callback_data="adm_kyc"),
            ],
            [InlineKeyboardButton(text=t["btn_broadcast"], callback_data="adm_broadcast")],
            [InlineKeyboardButton(text=f"« {back}", callback_data="adm_menu_home")],
        ])

    if menu == "treasury":
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=t["btn_credit"], callback_data="adm_credit"),
                InlineKeyboardButton(text=t["btn_debit"], callback_data="adm_debit"),
            ],
            [InlineKeyboardButton(text=t.get("btn_hot", "وضعیت ولت داغ" if fa else "Hot wallet"), callback_data="adm_hot_wallet")],
            [InlineKeyboardButton(text=t.get("btn_backup", "بکاپ" if fa else "Backups"), callback_data="adm_backups")],
            [InlineKeyboardButton(text=f"« {back}", callback_data="adm_menu_home")],
        ])

    # home root — categories only
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("کنسول وب ادمین" if fa else "Admin Web Console"), web_app=WebAppInfo(url=admin_webapp_url(uid)))],
        [
            InlineKeyboardButton(text=("بروزرسانی" if fa else "Refresh"), callback_data="adm_refresh"),
            InlineKeyboardButton(text=("English" if fa else "فارسی"), callback_data="adm_lang"),
        ],
        [InlineKeyboardButton(text=("سیستم و امنیت" if fa else "System & Security"), callback_data="adm_menu_system")],
        [InlineKeyboardButton(text=("عملیات و صف‌ها" if fa else "Operations & Queues"), callback_data="adm_menu_ops")],
        [InlineKeyboardButton(text=("کاربران و KYC" if fa else "Users & KYC"), callback_data="adm_menu_users")],
        [InlineKeyboardButton(text=("خزانه و مالی" if fa else "Treasury & Finance"), callback_data="adm_menu_treasury")],
        [
            InlineKeyboardButton(text=("گزارش روز" if fa else "Daily report"), callback_data="adm_daily"),
            InlineKeyboardButton(text=("آمار زنده" if fa else "Live metrics"), callback_data="adm_metrics"),
        ],
    ])



async def _stats_text(uid: int) -> str:
    """Premium framed admin dashboard (Telegram monospace-friendly)."""
    import aiosqlite
    from datetime import datetime
    fa = _lang(uid) == "fa"
    tmap = _t(uid)

    users = bal_ton = bal_usdt = 0.0
    pending_wd = pending_amt = open_bin = open_tickets = active_prop = 0
    loans = 0.0
    today_users = today_tx = 0
    kyc_pending = 0
    try:
        async with aiosqlite.connect(settings.db_name) as db:
            users = int((await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0] or 0)
            row = await (await db.execute(
                "SELECT COALESCE(SUM(balance),0), COALESCE(SUM(usdt_balance),0) FROM users"
            )).fetchone()
            bal_ton = float(row[0] or 0)
            bal_usdt = float(row[1] or 0)
            try:
                row = await (await db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM requests WHERE req_type='withdraw' AND status='pending'"
                )).fetchone()
                pending_wd = int(row[0] or 0)
                pending_amt = float(row[1] or 0)
            except Exception:
                pass
            try:
                open_bin = int((await (await db.execute(
                    "SELECT COUNT(*) FROM binary_trades WHERE status='open'"
                )).fetchone())[0] or 0)
            except Exception:
                pass
            try:
                active_prop = int((await (await db.execute(
                    "SELECT COUNT(*) FROM prop_accounts WHERE status='active'"
                )).fetchone())[0] or 0)
            except Exception:
                try:
                    active_prop = int((await (await db.execute(
                        "SELECT COUNT(*) FROM prop_challenges WHERE status='active'"
                    )).fetchone())[0] or 0)
                except Exception:
                    pass
            try:
                loans = float((await (await db.execute(
                    "SELECT COALESCE(SUM(loan_amount),0) FROM users WHERE loan_amount>0"
                )).fetchone())[0] or 0)
            except Exception:
                pass
            try:
                open_tickets = int((await (await db.execute(
                    "SELECT COUNT(*) FROM tickets WHERE status='open'"
                )).fetchone())[0] or 0)
            except Exception:
                pass
            day0 = datetime.utcnow().strftime("%Y-%m-%d")
            try:
                today_users = int((await (await db.execute(
                    "SELECT COUNT(*) FROM users WHERE date(join_date)=date(?)", (day0,)
                )).fetchone())[0] or 0)
            except Exception:
                pass
            try:
                today_tx = int((await (await db.execute(
                    "SELECT COUNT(*) FROM transactions WHERE date(created_at)=date(?)", (day0,)
                )).fetchone())[0] or 0)
            except Exception:
                pass
            try:
                kyc_pending = int((await (await db.execute(
                    "SELECT COUNT(*) FROM kyc_submissions WHERE status='pending'"
                )).fetchone())[0] or 0)
            except Exception:
                pass
    except Exception:
        pass

    freeze = tmap["on"] if settings.emergency_freeze else tmap["off"]
    maint = tmap["on"] if getattr(settings, "maintenance_mode", False) else tmap["off"]
    ver = getattr(settings, "app_version", "1.0.0") or "1.0.0"
    try:
        from bot.services.admin_2fa import is_2fa_enabled
        twofa = is_2fa_enabled()
    except Exception:
        twofa = False
    try:
        from bot.services.ton_chain import hot_wallet_configured
        hot = hot_wallet_configured()
    except Exception:
        hot = False

    def box(title: str, lines: list[str]) -> str:
        # Telegram: use pre block for alignment
        body = "\n".join(lines)
        return f"**{title}**\n```\n{body}\n```"

    if fa:
        alerts = []
        if pending_wd:
            alerts.append(f"• برداشت در صف: {pending_wd} ({pending_amt:.2f} TON)")
        if kyc_pending:
            alerts.append(f"• KYC معلق: {kyc_pending}")
        if settings.emergency_freeze:
            alerts.append("• قفل اضطراری فعال")
        if settings.maintenance_mode:
            alerts.append("• حالت تعمیر فعال")
        if not alerts:
            alerts.append("• مورد فوری نیست")

        b1 = box("خلاصه زنده", [
            f"کاربران      {users:>8}",
            f"امروز         {today_users:>8}",
            f"دفترکل TON  {bal_ton:>10.2f}",
            f"دفترکل USDT {bal_usdt:>10.2f}",
        ])
        b2 = box("صف‌ها", [
            f"برداشت معلق  {pending_wd:>6}  ({pending_amt:.2f})",
            f"تیکت باز     {open_tickets:>6}",
            f"باینری باز   {open_bin:>6}",
            f"پراپ فعال    {active_prop:>6}",
            f"وام          {loans:>10.2f}",
            f"KYC معلق     {kyc_pending:>6}",
        ])
        b3 = box("وضعیت سیستم", [
            f"قفل اضطراری  {freeze}",
            f"حالت تعمیر   {maint}",
            f"۲FA ادمین    {'فعال' if twofa else 'خاموش'}",
            f"ولت داغ      {'آماده' if hot else 'خاموش'}",
            f"تراکنش امروز {today_tx:>6}",
        ])
        b4 = box("هشدارها", alerts)
        return (
            f"**▸ مرکز کنترل CX**  `v{ver}`\n\n"
            f"{b1}\n{b2}\n{b3}\n{b4}\n"
            f"_از منوی زیر بخش مورد نظر را باز کنید._"
        )

    alerts = []
    if pending_wd:
        alerts.append(f"• Pending WD: {pending_wd} ({pending_amt:.2f} TON)")
    if kyc_pending:
        alerts.append(f"• KYC pending: {kyc_pending}")
    if settings.emergency_freeze:
        alerts.append("• Emergency freeze ON")
    if settings.maintenance_mode:
        alerts.append("• Maintenance ON")
    if not alerts:
        alerts.append("• No urgent items")

    b1 = box("Live summary", [
        f"Users         {users:>8}",
        f"Today         {today_users:>8}",
        f"Ledger TON  {bal_ton:>10.2f}",
        f"Ledger USDT {bal_usdt:>10.2f}",
    ])
    b2 = box("Queues", [
        f"Pending WD    {pending_wd:>6}  ({pending_amt:.2f})",
        f"Open tickets  {open_tickets:>6}",
        f"Open binary   {open_bin:>6}",
        f"Active prop   {active_prop:>6}",
        f"Loans         {loans:>10.2f}",
        f"KYC pending   {kyc_pending:>6}",
    ])
    b3 = box("System", [
        f"Freeze        {freeze}",
        f"Maintenance   {maint}",
        f"Admin 2FA     {'ON' if twofa else 'OFF'}",
        f"Hot wallet    {'ready' if hot else 'off'}",
        f"Tx today      {today_tx:>6}",
    ])
    b4 = box("Alerts", alerts)
    return (
        f"**▸ CX Control Center**  `v{ver}`\n\n"
        f"{b1}\n{b2}\n{b3}\n{b4}\n"
        f"_Open a section from the menu below._"
    )



@router.message(F.text.in_({"پنل مدیریت", "Admin Hub", "/admin"}), StateFilter("*"))
async def admin_dashboard(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    await state.clear()
    uid = message.from_user.id
    await message.answer(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")



@router.callback_query(F.data == "adm_menu_home", StateFilter("*"))
async def cb_menu_home(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_t(callback.from_user.id)["denied"], show_alert=True)
    uid = callback.from_user.id
    try:
        await callback.message.edit_text(await _stats_text(uid), reply_markup=admin_kb(uid, "home"), parse_mode="Markdown")
    except Exception:
        await callback.message.answer(await _stats_text(uid), reply_markup=admin_kb(uid, "home"), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "adm_menu_system", StateFilter("*"))
async def cb_menu_system(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_t(callback.from_user.id)["denied"], show_alert=True)
    uid = callback.from_user.id
    fa = _lang(uid) == "fa"
    title = "سیستم" if fa else "System"
    try:
        await callback.message.edit_text(title + "\n\n" + await _stats_text(uid), reply_markup=admin_kb(uid, "system"), parse_mode="Markdown")
    except Exception:
        await callback.message.answer(title, reply_markup=admin_kb(uid, "system"))
    await callback.answer()


@router.callback_query(F.data == "adm_menu_ops", StateFilter("*"))
async def cb_menu_ops(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_t(callback.from_user.id)["denied"], show_alert=True)
    uid = callback.from_user.id
    title = "عملیات" if _lang(uid) == "fa" else "Operations"
    try:
        await callback.message.edit_text(title, reply_markup=admin_kb(uid, "ops"))
    except Exception:
        await callback.message.answer(title, reply_markup=admin_kb(uid, "ops"))
    await callback.answer()


@router.callback_query(F.data == "adm_menu_users", StateFilter("*"))
async def cb_menu_users(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_t(callback.from_user.id)["denied"], show_alert=True)
    uid = callback.from_user.id
    title = "کاربران" if _lang(uid) == "fa" else "Users"
    try:
        await callback.message.edit_text(title, reply_markup=admin_kb(uid, "users"))
    except Exception:
        await callback.message.answer(title, reply_markup=admin_kb(uid, "users"))
    await callback.answer()


@router.callback_query(F.data == "adm_menu_treasury", StateFilter("*"))
async def cb_menu_treasury(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_t(callback.from_user.id)["denied"], show_alert=True)
    uid = callback.from_user.id
    title = "خزانه" if _lang(uid) == "fa" else "Treasury"
    try:
        await callback.message.edit_text(title, reply_markup=admin_kb(uid, "treasury"))
    except Exception:
        await callback.message.answer(title, reply_markup=admin_kb(uid, "treasury"))
    await callback.answer()


@router.callback_query(F.data == "adm_metrics", StateFilter("*"))
async def cb_metrics(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_t(callback.from_user.id)["denied"], show_alert=True)
    uid = callback.from_user.id
    fa = _lang(uid) == "fa"
    try:
        from bot.services.monitoring import snapshot
        snap = snapshot()
        counters = snap.get("counters") or {}
        labels = {
            "chain_errors": ("خطای زنجیره", "Chain errors"),
            "withdraw_auto_ok": ("برداشت خودکار OK", "Auto withdraw OK"),
            "withdraw_auto_fail": ("برداشت خودکار Fail", "Auto withdraw fail"),
            "deposits_credited": ("واریز ثبت‌شده", "Deposits credited"),
            "binary_settled": ("باینری تسویه", "Binary settled"),
            "swaps": ("سواپ", "Swaps"),
        }
        rows = [f"{'آپ‌تایم' if fa else 'Uptime':<16} {snap.get('uptime_sec', 0)}s"]
        for k, v in counters.items():
            lab = labels.get(k, (k, k))
            rows.append(f"{(lab[0] if fa else lab[1]):<16} {v}")
        body = "\n".join(rows)
        ver = getattr(settings, "app_version", "?")
        title = "آمار زنده" if fa else "Live metrics"
        text = f"**{title}**  `v{ver}`\n```\n{body}\n```"
    except Exception as exc:
        text = str(exc)
    try:
        await callback.message.edit_text(text, reply_markup=admin_kb(uid, "system"), parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=admin_kb(uid, "system"), parse_mode="Markdown")
    await callback.answer()



@router.callback_query(F.data == "adm_hot_wallet", StateFilter("*"))
async def cb_hot_wallet(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_t(callback.from_user.id)["denied"], show_alert=True)
    uid = callback.from_user.id
    fa = _lang(uid) == "fa"
    try:
        from bot.services.ton_chain import hot_wallet_configured
        ok = bool(hot_wallet_configured())
    except Exception:
        ok = False
    text = ("ولت داغ: پیکربندی شده" if ok else "ولت داغ: پیکربندی نشده") if fa else (
        "Hot wallet: configured" if ok else "Hot wallet: not configured"
    )
    text += f"\nauto_max={getattr(settings,'auto_withdraw_max',0)} onchain={getattr(settings,'withdraw_onchain_enabled',False)}"
    try:
        await callback.message.edit_text(text, reply_markup=admin_kb(uid, "treasury"))
    except Exception:
        await callback.message.answer(text, reply_markup=admin_kb(uid, "treasury"))
    await callback.answer()


@router.callback_query(F.data == "adm_backups", StateFilter("*"))
async def cb_backups(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_t(callback.from_user.id)["denied"], show_alert=True)
    uid = callback.from_user.id
    fa = _lang(uid) == "fa"
    try:
        from bot.workers.backup import list_backups
        rows = list_backups(5)
        text = ("بکاپ نیست" if fa else "No backups") if not rows else (
            ("بکاپ‌های اخیر:\n" if fa else "Recent backups:\n") + "\n".join(str(r)[:120] for r in rows)
        )
    except Exception as exc:
        text = str(exc)
    try:
        await callback.message.edit_text(text, reply_markup=admin_kb(uid, "treasury"))
    except Exception:
        await callback.message.answer(text, reply_markup=admin_kb(uid, "treasury"))
    await callback.answer()


@router.callback_query(F.data == "adm_noop", StateFilter("*"))
async def cb_noop(callback: CallbackQuery):
    await callback.answer()



@router.message(StateFilter(UserStates.waiting_for_admin_2fa))
async def process_admin_2fa(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        await state.clear()
        return
    code = (message.text or "").strip()
    if not _admin_2fa_ok(code):
        await message.answer(_toast(message.from_user.id, "کد ۲FA نامعتبر", "Invalid 2FA code"))
        return
    import time as _time
    await state.update_data(admin_2fa_ok_until=_time.time() + 300)
    _admin_2fa_session_grant(message.from_user.id, 300)
    data = await state.get_data()
    nxt = data.get("admin_2fa_next") or "home"
    await state.set_state(None)
    await message.answer(_toast(message.from_user.id, "تأیید ۲FA", "2FA OK"))
    # re-open home
    try:
        await message.answer(await _stats_text(message.from_user.id), reply_markup=admin_kb(message.from_user.id, "home"), parse_mode="Markdown")
    except Exception:
        pass



@router.callback_query(F.data == "adm_2fa_unlock", StateFilter("*"))
async def cb_2fa_unlock(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
    from bot.services.admin_2fa import is_2fa_enabled
    if not is_2fa_enabled():
        await callback.answer(_toast(callback.from_user.id, "۲FA پیکربندی نشده", "2FA not configured"), show_alert=True)
        return
    await state.set_state(UserStates.waiting_for_admin_2fa)
    fa = _lang(callback.from_user.id) == "fa"
    await callback.message.answer(_toast(callback.from_user.id, "کد ۶ رقمی Authenticator را بفرستید:", "Enter 6-digit Authenticator code:"))
    await callback.answer()

@router.callback_query(F.data == "adm_lang", StateFilter("*"))
async def cb_lang(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
    uid = callback.from_user.id
    # Toggle: button shows the language you will switch TO
    new_lang = "en" if _lang(uid) == "fa" else "fa"
    _admin_lang[uid] = new_lang
    try:
        await callback.message.edit_text(
            await _stats_text(uid),
            reply_markup=admin_kb(uid, "home"),
            parse_mode="Markdown",
        )
    except Exception:
        await callback.message.answer(
            await _stats_text(uid),
            reply_markup=admin_kb(uid, "home"),
            parse_mode="Markdown",
        )
    tip = "Language: English" if new_lang == "en" else "زبان: فارسی"
    await callback.answer(tip)


@router.callback_query(F.data == "adm_refresh", StateFilter("*"))
async def cb_refresh(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
    await state.clear()
    uid = callback.from_user.id
    try:
        await callback.message.edit_text(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")
    except Exception:
        await callback.message.answer(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")
    await callback.answer(_t(uid)["updated"])


@router.callback_query(F.data == "adm_toggle_freeze", StateFilter("*"))
async def cb_freeze(callback: CallbackQuery):
    if not _admin_2fa_session_ok(callback.from_user.id):
        await callback.answer(_toast(callback.from_user.id, "نیاز به ۲FA — سیستم → باز کردن ۲FA", "2FA required — System → Unlock 2FA"), show_alert=True)
        return
    from bot.services.admin_2fa import is_2fa_enabled
    if is_2fa_enabled():
        st = callback.bot  # placeholder
    # 2FA: use recent window via FSM — simplified gate via data store
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
    settings.emergency_freeze = not bool(settings.emergency_freeze)
    uid = callback.from_user.id
    await callback.answer(f"{_t(uid)['freeze']}: {settings.emergency_freeze}", show_alert=True)
    try:
        await callback.message.edit_text(await _stats_text(uid), reply_markup=admin_kb(uid), parse_mode="Markdown")
    except Exception:
        pass


@router.callback_query(F.data == "adm_toggle_maint", StateFilter("*"))
async def cb_maint(callback: CallbackQuery):
    if not _admin_2fa_session_ok(callback.from_user.id):
        await callback.answer(_toast(callback.from_user.id, "نیاز به ۲FA — سیستم → باز کردن ۲FA", "2FA required — System → Unlock 2FA"), show_alert=True)
        return
    from bot.services.admin_2fa import is_2fa_enabled
    if is_2fa_enabled():
        st = callback.bot  # placeholder
    # 2FA: use recent window via FSM — simplified gate via data store
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
    if not _admin_2fa_session_ok(callback.from_user.id):
        await callback.answer(_toast(callback.from_user.id, "نیاز به ۲FA", "2FA required"), show_alert=True)
        return
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
    rid = int(callback.data.split("_")[-1])
    t = _t(callback.from_user.id)
    try:
        from bot.services.withdraw import settle_withdraw_onchain
        await callback.answer(_toast(callback.from_user.id, "در حال ارسال…", "Sending…"))
        res = await settle_withdraw_onchain(rid)
        if not res.get("ok"):
            return await callback.answer(str(res.get("error") or res.get("reason")), show_alert=True)
        note = res.get("tx_hash") or ""
        await callback.message.edit_text(
            (callback.message.text or "") + "\n\n" + t["approved"] + "\n`" + str(note) + "`"
        )
    except Exception as e:
        await callback.answer(str(e), show_alert=True)



@router.callback_query(F.data.startswith("adm_wd_no_"), StateFilter("*"))
async def cb_wd_no(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
    tid = int(callback.data.split("_")[-1])
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE support_tickets SET status='closed' WHERE ticket_id=?", (tid,))
        await db.commit()
    await callback.message.edit_text((callback.message.text or "") + "\n\nClosed.")
    await callback.answer(_toast(callback.from_user.id, "انجام شد", "OK"))


@router.callback_query(F.data == "adm_open_binary", StateFilter("*"))
async def cb_open_binary(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
    uid = callback.from_user.id
    t = _t(uid)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        c0 = (await (await db.execute("SELECT COUNT(*) FROM users WHERE kyc_level=0")).fetchone())[0]
        c1 = (await (await db.execute("SELECT COUNT(*) FROM users WHERE kyc_level=1")).fetchone())[0]
        c2 = (await (await db.execute("SELECT COUNT(*) FROM users WHERE kyc_level=2")).fetchone())[0]
        rows = await (await db.execute(
            """
            SELECT user_id, phone, email, kyc_level, balance, join_date
            FROM users
            WHERE kyc_level IN (0, 1)
            ORDER BY kyc_level DESC, user_id DESC
            LIMIT 25
            """
        )).fetchall()
    summary = "**KYC**\nL0: `%s` · L1: `%s` · L2: `%s`" % (c0, c1, c2)
    await callback.message.answer(summary, parse_mode="Markdown")
    if not rows:
        return await callback.answer(t["no_kyc"], show_alert=True)
    for r in rows:
        u = r["user_id"]
        lvl = int(r["kyc_level"] or 0)
        text = (
            "User `%s` · **L%s**\n"
            "Phone: `%s`\n"
            "Email: `%s`\n"
            "Balance: `%.4f` TON\n"
            "Joined: `%s`"
        ) % (
            u,
            lvl,
            r["phone"] or "-",
            r["email"] or "-",
            float(r["balance"] or 0),
            r["join_date"] or "-",
        )
        buttons = []
        if lvl < 1:
            buttons.append(InlineKeyboardButton(text="Set L1", callback_data="adm_kyc_set_%s_1" % u))
        if lvl < 2:
            buttons.append(InlineKeyboardButton(text="Approve L2", callback_data="adm_kyc_accept_%s" % u))
        buttons.append(InlineKeyboardButton(text="Set L0", callback_data="adm_kyc_set_%s_0" % u))
        if lvl >= 1:
            buttons.append(InlineKeyboardButton(text="Reject to L0", callback_data="adm_kyc_reject_%s" % u))
        rows_kb = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        kb = InlineKeyboardMarkup(inline_keyboard=rows_kb)
        await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("adm_kyc_set_"), StateFilter("*"))
async def cb_kyc_set(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
    parts = callback.data.split("_")
    target_id = int(parts[3])
    level = int(parts[4])
    if level not in (0, 1, 2):
        return await callback.answer(_toast(callback.from_user.id, "سطح نامعتبر", "Bad level"), show_alert=True)
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE users SET kyc_level = ? WHERE user_id = ?", (level, target_id))
        await db.commit()
    await callback.message.edit_text((callback.message.text or "") + "\n\n-> KYC set to L%s" % level)
    await callback.answer(_toast(callback.from_user.id, "سطح L%s" % level, "L%s" % level))
    try:
        if level >= 2:
            msg = "KYC Level 2 approved." if _lang(callback.from_user.id) == "en" else "احراز هویت سطح ۲ تأیید شد."
            await callback.bot.send_message(target_id, msg)
        elif level == 0:
            msg = "KYC reset to Level 0." if _lang(callback.from_user.id) == "en" else "سطح احراز به ۰ بازگردانده شد."
            await callback.bot.send_message(target_id, msg)
    except Exception:
        pass


@router.callback_query(F.data == "adm_lookup", StateFilter("*"))
async def cb_lookup(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
        return await callback.answer(_toast(callback.from_user.id, "دسترسی ندارید", "Denied"), show_alert=True)
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
