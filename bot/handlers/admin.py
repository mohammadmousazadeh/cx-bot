"""Telegram Admin Hub — only ADMIN_ID can access."""
from __future__ import annotations

import logging

import aiosqlite
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import settings
from bot.db.connection import get_db
from bot.db.ledger import (
    TxKind,
    BusinessRuleError,
    InsufficientBalance,
    complete_withdraw,
    credit_ton,
    debit_ton,
    get_balances,
    reject_withdraw,
)
from bot.states import UserStates

logger = logging.getLogger("cx.admin")
router = Router(name="admin")


def _admin_only(user_id: int) -> bool:
    return int(user_id) == int(settings.admin_id)


def admin_kb() -> InlineKeyboardMarkup:
    freeze = "ON" if settings.emergency_freeze else "OFF"
    maint = "ON" if getattr(settings, "maintenance_mode", False) else "OFF"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Refresh stats", callback_data="adm_refresh"),
            ],
            [
                InlineKeyboardButton(text=f"Emergency freeze: {freeze}", callback_data="adm_toggle_freeze"),
                InlineKeyboardButton(text=f"Maintenance: {maint}", callback_data="adm_toggle_maint"),
            ],
            [
                InlineKeyboardButton(text="Pending withdraws", callback_data="adm_withdraws"),
                InlineKeyboardButton(text="Open binary trades", callback_data="adm_open_binary"),
            ],
            [
                InlineKeyboardButton(text="Lookup user", callback_data="adm_lookup"),
                InlineKeyboardButton(text="Credit TON", callback_data="adm_credit"),
            ],
            [
                InlineKeyboardButton(text="Debit TON", callback_data="adm_debit"),
                InlineKeyboardButton(text="Broadcast", callback_data="adm_broadcast"),
            ],
            [
                InlineKeyboardButton(text="Prop overview", callback_data="adm_prop"),
                InlineKeyboardButton(text="Recent txs", callback_data="adm_recent_tx"),
            ],
        ]
    )


async def _stats_text() -> str:
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        total_users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        bal = await (await db.execute("SELECT COALESCE(SUM(balance),0), COALESCE(SUM(usdt_balance),0) FROM users")).fetchone()
        ton_sum, usdt_sum = float(bal[0] or 0), float(bal[1] or 0)
        pending_wd = (await (await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM requests WHERE req_type='withdraw' AND status='pending'"
        )).fetchone())
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

    return (
        "**CX Admin Hub**\n\n"
        f"Users: `{total_users}`\n"
        f"Ledger TON: `{ton_sum:,.2f}`\n"
        f"Ledger USDT: `{usdt_sum:,.2f}`\n"
        f"Pending withdraws: `{pending_wd[0]}` (`{float(pending_wd[1] or 0):,.2f}` TON)\n"
        f"Open binary: `{open_bin}`\n"
        f"Active prop: `{prop_active}`\n"
        f"Loans outstanding: `{float(loans or 0):,.2f}` TON\n"
        f"Freeze: `{'ON' if settings.emergency_freeze else 'OFF'}`\n"
        f"Maintenance: `{'ON' if getattr(settings, 'maintenance_mode', False) else 'OFF'}`"
    )


@router.message(F.text.in_({"پنل مدیریت", "Admin Hub", "/admin"}), StateFilter("*"))
async def admin_dashboard(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    await state.clear()
    await message.answer(await _stats_text(), reply_markup=admin_kb(), parse_mode="Markdown")


@router.callback_query(F.data == "adm_refresh", StateFilter("*"))
async def cb_refresh(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    await state.clear()
    try:
        await callback.message.edit_text(await _stats_text(), reply_markup=admin_kb(), parse_mode="Markdown")
    except Exception:
        await callback.message.answer(await _stats_text(), reply_markup=admin_kb(), parse_mode="Markdown")
    await callback.answer("Updated")


@router.callback_query(F.data == "adm_toggle_freeze", StateFilter("*"))
async def cb_freeze(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    settings.emergency_freeze = not bool(settings.emergency_freeze)
    await callback.answer(f"Freeze = {settings.emergency_freeze}", show_alert=True)
    try:
        await callback.message.edit_text(await _stats_text(), reply_markup=admin_kb(), parse_mode="Markdown")
    except Exception:
        pass


@router.callback_query(F.data == "adm_toggle_maint", StateFilter("*"))
async def cb_maint(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    settings.maintenance_mode = not bool(getattr(settings, "maintenance_mode", False))
    await callback.answer(f"Maintenance = {settings.maintenance_mode}", show_alert=True)
    try:
        await callback.message.edit_text(await _stats_text(), reply_markup=admin_kb(), parse_mode="Markdown")
    except Exception:
        pass


@router.callback_query(F.data == "adm_withdraws", StateFilter("*"))
async def cb_withdraws(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT request_id, user_id, amount, address, created_at
            FROM requests
            WHERE req_type='withdraw' AND status='pending'
            ORDER BY request_id ASC LIMIT 10
            """
        )
        rows = await cur.fetchall()
    if not rows:
        return await callback.answer("No pending withdraws", show_alert=True)

    for r in rows:
        rid = r["request_id"]
        text = (
            f"Withdraw `#{rid}`\n"
            f"User: `{r['user_id']}`\n"
            f"Amount: `{float(r['amount']):.4f}` TON\n"
            f"Address:\n`{r['address']}`\n"
            f"At: `{r['created_at']}`"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Approve", callback_data=f"adm_wd_ok_{rid}"),
                    InlineKeyboardButton(text="Reject+refund", callback_data=f"adm_wd_no_{rid}"),
                ]
            ]
        )
        await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("adm_wd_ok_"), StateFilter("*"))
async def cb_wd_ok(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    rid = int(callback.data.split("_")[-1])
    try:
        await complete_withdraw(rid, tx_hash=f"admin_{rid}")
        await callback.message.edit_text(callback.message.text + "\n\nApproved by admin.")
        await callback.answer("Approved")
    except Exception as e:
        await callback.answer(str(e), show_alert=True)


@router.callback_query(F.data.startswith("adm_wd_no_"), StateFilter("*"))
async def cb_wd_no(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    rid = int(callback.data.split("_")[-1])
    try:
        res = await reject_withdraw(rid)
        await callback.message.edit_text(
            (callback.message.text or "") + f"\n\nRejected. Refunded. Bal `{res.ton_balance:.4f}`"
        )
        await callback.answer("Rejected + refunded")
    except Exception as e:
        await callback.answer(str(e), show_alert=True)


@router.callback_query(F.data == "adm_open_binary", StateFilter("*"))
async def cb_open_binary(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT id, user_id, symbol, direction, amount, entry_price, expire_time
            FROM binary_trades WHERE status='open' ORDER BY id DESC LIMIT 15
            """
        )
        rows = await cur.fetchall()
    if not rows:
        return await callback.answer("No open binary trades", show_alert=True)
    lines = ["**Open binary trades**\n"]
    for r in rows:
        lines.append(
            f"#{r['id']} u`{r['user_id']}` {r['direction']} {r['amount']} {r['symbol']} @ {r['entry_price']} exp `{r['expire_time']}`"
        )
    await callback.message.answer("\n".join(lines), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "adm_prop", StateFilter("*"))
async def cb_prop(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute(
                """
                SELECT user_id, plan_size, virtual_balance, status, trades_count
                FROM prop_accounts ORDER BY updated_at DESC LIMIT 15
                """
            )
            rows = await cur.fetchall()
        except Exception:
            rows = []
    if not rows:
        return await callback.answer("No prop accounts", show_alert=True)
    lines = ["**Prop accounts**\n"]
    for r in rows:
        lines.append(
            f"u`{r['user_id']}` plan `{r['plan_size']}` virt `{r['virtual_balance']:.2f}` "
            f"`{r['status']}` trades `{r['trades_count'] if 'trades_count' in r.keys() else '-'}`"
        )
    await callback.message.answer("\n".join(lines), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "adm_recent_tx", StateFilter("*"))
async def cb_recent_tx(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT id, user_id, kind, amount, currency, created_at
            FROM transactions ORDER BY id DESC LIMIT 20
            """
        )
        rows = await cur.fetchall()
    if not rows:
        return await callback.answer("No transactions", show_alert=True)
    lines = ["**Recent ledger**\n"]
    for r in rows:
        lines.append(
            f"#{r['id']} u`{r['user_id']}` `{r['kind']}` `{r['amount']}` {r['currency']} `{r['created_at']}`"
        )
    await callback.message.answer("\n".join(lines), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "adm_lookup", StateFilter("*"))
async def cb_lookup(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    await state.set_state(UserStates.waiting_for_admin_lookup)
    await callback.message.answer("Send user id to lookup:")
    await callback.answer()


@router.message(StateFilter(UserStates.waiting_for_admin_lookup))
async def process_lookup(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
    except Exception:
        return await message.answer("Invalid user id")
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT user_id, balance, usdt_balance, kyc_level, loan_amount, lang, phone, email FROM users WHERE user_id=?",
            (uid,),
        )
        row = await cur.fetchone()
    await state.clear()
    if not row:
        return await message.answer("User not found")
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
    await message.answer(text, parse_mode="Markdown", reply_markup=admin_kb())


@router.callback_query(F.data == "adm_credit", StateFilter("*"))
async def cb_credit(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    await state.set_state(UserStates.waiting_for_admin_add_balance)
    await callback.message.answer("Credit format:\n`user_id amount`\nor just `amount` (credit yourself)", parse_mode="Markdown")
    await callback.answer()


@router.message(StateFilter(UserStates.waiting_for_admin_add_balance))
async def process_credit(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    try:
        parts = message.text.strip().split()
        if len(parts) >= 2:
            target_id = int(parts[0])
            amt = float(parts[1])
        else:
            target_id = message.from_user.id
            amt = float(parts[0])
        if amt <= 0:
            return await message.answer("Amount must be positive")
        res = await credit_ton(
            target_id,
            amt,
            kind=TxKind.ADMIN_CREDIT,
            meta={"by_admin": message.from_user.id},
        )
        await state.clear()
        await message.answer(
            f"Credited user `{target_id}` +`{amt:g}` TON\nBalance: `{res.ton_balance:g}`",
            parse_mode="Markdown",
            reply_markup=admin_kb(),
        )
        try:
            await message.bot.send_message(target_id, f"Admin credited +{amt:g} TON to your balance.")
        except Exception:
            pass
    except Exception as e:
        await message.answer(f"Error: {e}")


@router.callback_query(F.data == "adm_debit", StateFilter("*"))
async def cb_debit(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    await state.set_state(UserStates.waiting_for_admin_debit)
    await callback.message.answer("Debit format:\n`user_id amount`", parse_mode="Markdown")
    await callback.answer()


@router.message(StateFilter(UserStates.waiting_for_admin_debit))
async def process_debit(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    try:
        parts = message.text.strip().split()
        target_id = int(parts[0])
        amt = float(parts[1])
        if amt <= 0:
            return await message.answer("Amount must be positive")
        res = await debit_ton(
            target_id,
            amt,
            kind=TxKind.ADMIN_DEBIT,
            meta={"by_admin": message.from_user.id},
        )
        await state.clear()
        await message.answer(
            f"Debited user `{target_id}` -`{amt:g}` TON\nBalance: `{res.ton_balance:g}`",
            parse_mode="Markdown",
            reply_markup=admin_kb(),
        )
    except InsufficientBalance:
        await message.answer("Insufficient user balance")
    except Exception as e:
        await message.answer(f"Error: {e}")


@router.callback_query(F.data == "adm_broadcast", StateFilter("*"))
async def cb_broadcast(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer("Denied", show_alert=True)
    await state.set_state(UserStates.waiting_for_admin_broadcast)
    await callback.message.answer("Send broadcast message text (or /cancel):")
    await callback.answer()


@router.message(StateFilter(UserStates.waiting_for_admin_broadcast))
async def process_broadcast(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    if (message.text or "").strip().lower() in ("/cancel", "cancel"):
        await state.clear()
        return await message.answer("Cancelled", reply_markup=admin_kb())
    text = message.text or ""
    await state.clear()
    async with aiosqlite.connect(settings.db_name) as db:
        cur = await db.execute("SELECT user_id FROM users")
        ids = [r[0] for r in await cur.fetchall()]
    ok = fail = 0
    await message.answer(f"Broadcasting to {len(ids)} users…")
    for uid in ids:
        try:
            await message.bot.send_message(uid, text)
            ok += 1
        except Exception:
            fail += 1
    await message.answer(f"Broadcast done. OK `{ok}` / fail `{fail}`", parse_mode="Markdown", reply_markup=admin_kb())


# Legacy callback compatibility
@router.callback_query(F.data == "toggle_emergency_freeze", StateFilter("*"))
async def legacy_freeze(callback: CallbackQuery):
    await cb_freeze(callback)


@router.callback_query(F.data == "admin_add_bal", StateFilter("*"))
async def legacy_add_bal(callback: CallbackQuery, state: FSMContext):
    await cb_credit(callback, state)
