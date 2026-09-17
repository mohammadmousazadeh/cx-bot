"""
Central financial ledger.

ALL balance changes must go through this module.
Never run raw `UPDATE users SET balance = ...` from handlers.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

from bot.db.connection import get_db


class TxKind(str, Enum):
    WELCOME_BONUS = "welcome_bonus"
    DEPOSIT = "deposit"
    WITHDRAW_HOLD = "withdraw_hold"
    WITHDRAW_RELEASE = "withdraw_release"  # rejected withdraw — money back
    WITHDRAW_COMPLETE = "withdraw_complete"  # admin settled (already held)
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    SWAP_TON_OUT = "swap_ton_out"
    SWAP_USDT_IN = "swap_usdt_in"
    SWAP_USDT_OUT = "swap_usdt_out"
    SWAP_TON_IN = "swap_ton_in"
    STAKE_LOCK = "stake_lock"
    STAKE_REWARD = "stake_reward"
    STAKE_UNLOCK = "stake_unlock"
    LOAN_CREDIT = "loan_credit"
    LOAN_REPAY = "loan_repay"
    MISSION_REWARD = "mission_reward"
    DAILY_BONUS = "daily_bonus"
    PREDICT_BET = "predict_bet"
    PREDICT_WIN = "predict_win"
    VIP_PURCHASE = "vip_purchase"
    BINARY_BET = "binary_bet"
    BINARY_WIN = "binary_win"
    BINARY_REFUND = "binary_refund"
    PROP_FEE = "prop_fee"
    SNIPER_BET = "sniper_bet"
    SNIPER_WIN = "sniper_win"
    PROP_REWARD = "prop_reward"
    ADMIN_CREDIT = "admin_credit"
    ADMIN_DEBIT = "admin_debit"
    ADJUSTMENT = "adjustment"


class LedgerError(Exception):
    """Base ledger error."""


class InsufficientBalance(LedgerError):
    pass


class UserNotFound(LedgerError):
    pass


class DuplicateTransaction(LedgerError):
    pass


class InvalidAmount(LedgerError):
    pass


class BusinessRuleError(LedgerError):
    pass


@dataclass
class LedgerResult:
    ton_balance: float
    usdt_balance: float
    tx_id: int


def _meta_json(meta: Any) -> Optional[str]:
    if meta is None:
        return None
    if isinstance(meta, str):
        return meta
    return json.dumps(meta, ensure_ascii=False, default=str)


async def get_balances(user_id: int) -> tuple[float, float]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT balance, usdt_balance FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise UserNotFound(f"user {user_id} not found")
        return float(row["balance"] or 0.0), float(row["usdt_balance"] or 0.0)


async def apply_entry(
    user_id: int,
    *,
    kind: TxKind | str,
    ton_delta: float = 0.0,
    usdt_delta: float = 0.0,
    meta: Any = None,
    idempotency_key: str | None = None,
    allow_negative: bool = False,
    extra_user_updates: dict | None = None,
) -> LedgerResult:
    """
    Atomically apply balance deltas and write one ledger row.

    - Uses BEGIN IMMEDIATE for write lock
    - Rejects insufficient funds unless allow_negative=True
    - Supports idempotency_key to prevent double-credit (e.g. same chain tx)
    """
    if isinstance(kind, TxKind):
        kind = kind.value

    if not math.isfinite(ton_delta) or not math.isfinite(usdt_delta):
        raise InvalidAmount("non_finite_amount")

    if abs(ton_delta) < 1e-12 and abs(usdt_delta) < 1e-12 and not extra_user_updates:
        raise InvalidAmount("empty ledger entry")

    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            if idempotency_key:
                cur = await db.execute(
                    "SELECT id FROM transactions WHERE idempotency_key = ?",
                    (idempotency_key,),
                )
                if await cur.fetchone():
                    await db.execute("ROLLBACK")
                    raise DuplicateTransaction(idempotency_key)

            cur = await db.execute(
                "SELECT balance, usdt_balance FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = await cur.fetchone()
            if not row:
                await db.execute("ROLLBACK")
                raise UserNotFound(f"user {user_id} not found")

            ton = float(row["balance"] or 0.0)
            usdt = float(row["usdt_balance"] or 0.0)
            new_ton = ton + ton_delta
            new_usdt = usdt + usdt_delta

            if not allow_negative:
                if new_ton < -1e-9:
                    await db.execute("ROLLBACK")
                    raise InsufficientBalance(f"TON need {-ton_delta}, have {ton}")
                if new_usdt < -1e-9:
                    await db.execute("ROLLBACK")
                    raise InsufficientBalance(f"USDT need {-usdt_delta}, have {usdt}")

            # Clamp tiny negatives from float noise
            if abs(new_ton) < 1e-10:
                new_ton = 0.0
            if abs(new_usdt) < 1e-10:
                new_usdt = 0.0

            await db.execute(
                "UPDATE users SET balance = ?, usdt_balance = ? WHERE user_id = ?",
                (new_ton, new_usdt, user_id),
            )

            if extra_user_updates:
                allowed = {
                    "loan_amount", "is_vip", "vip_expire_date", "task_channel",
                    "task_invite", "task_youtube", "last_bonus_date", "kyc_level",
                    "phone", "email", "lang", "security_pin", "cooldown_until",
                    "whitelist_address", "referrer_id",
                }
                sets, vals = [], []
                for k, v in extra_user_updates.items():
                    if k not in allowed:
                        await db.execute("ROLLBACK")
                        raise ValueError(f"disallowed field in ledger: {k}")
                    sets.append(f"{k} = ?")
                    vals.append(v)
                vals.append(user_id)
                await db.execute(
                    f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?",
                    vals,
                )

            # Primary currency for the row is the non-zero delta side
            if abs(ton_delta) >= abs(usdt_delta):
                amount, currency, balance_after = ton_delta, "TON", new_ton
            else:
                amount, currency, balance_after = usdt_delta, "USDT", new_usdt

            cur = await db.execute(
                """
                INSERT INTO transactions
                    (user_id, kind, amount, currency, balance_after, meta, idempotency_key)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    kind,
                    amount,
                    currency,
                    balance_after,
                    _meta_json(meta),
                    idempotency_key,
                ),
            )
            tx_id = cur.lastrowid
            await db.commit()
            return LedgerResult(ton_balance=new_ton, usdt_balance=new_usdt, tx_id=tx_id)
        except Exception:
            try:
                await db.execute("ROLLBACK")
            except Exception:
                pass
            raise


async def credit_ton(
    user_id: int,
    amount: float,
    *,
    kind: TxKind,
    meta: Any = None,
    idempotency_key: str | None = None,
    extra_user_updates: dict | None = None,
) -> LedgerResult:
    if amount <= 0:
        raise InvalidAmount("credit amount must be positive")
    return await apply_entry(
        user_id,
        kind=kind,
        ton_delta=amount,
        meta=meta,
        idempotency_key=idempotency_key,
        extra_user_updates=extra_user_updates,
    )


async def debit_ton(
    user_id: int,
    amount: float,
    *,
    kind: TxKind,
    meta: Any = None,
    idempotency_key: str | None = None,
    extra_user_updates: dict | None = None,
) -> LedgerResult:
    if amount <= 0:
        raise InvalidAmount("debit amount must be positive")
    return await apply_entry(
        user_id,
        kind=kind,
        ton_delta=-amount,
        meta=meta,
        idempotency_key=idempotency_key,
        extra_user_updates=extra_user_updates,
    )


async def transfer_p2p(
    from_user: int,
    to_user: int,
    amount: float,
    *,
    meta: Any = None,
) -> tuple[LedgerResult, LedgerResult]:
    """Atomic P2P: debit sender + credit receiver in one SQLite transaction."""
    if amount <= 0:
        raise InvalidAmount("transfer amount must be positive")
    if from_user == to_user:
        raise BusinessRuleError("cannot transfer to self")

    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "SELECT balance, usdt_balance FROM users WHERE user_id = ?",
                (from_user,),
            )
            sender = await cur.fetchone()
            if not sender:
                await db.execute("ROLLBACK")
                raise UserNotFound(f"sender {from_user}")

            cur = await db.execute(
                "SELECT balance, usdt_balance FROM users WHERE user_id = ?",
                (to_user,),
            )
            receiver = await cur.fetchone()
            if not receiver:
                await db.execute("ROLLBACK")
                raise UserNotFound(f"receiver {to_user}")

            s_ton = float(sender["balance"] or 0.0)
            r_ton = float(receiver["balance"] or 0.0)
            if s_ton < amount - 1e-9:
                await db.execute("ROLLBACK")
                raise InsufficientBalance(f"have {s_ton}, need {amount}")

            s_new = s_ton - amount
            r_new = r_ton + amount
            s_usdt = float(sender["usdt_balance"] or 0.0)
            r_usdt = float(receiver["usdt_balance"] or 0.0)

            await db.execute(
                "UPDATE users SET balance = ? WHERE user_id = ?",
                (s_new, from_user),
            )
            await db.execute(
                "UPDATE users SET balance = ? WHERE user_id = ?",
                (r_new, to_user),
            )

            meta_out = _meta_json({"to": to_user, **(meta or {} if isinstance(meta, dict) else {"note": meta})})
            meta_in = _meta_json({"from": from_user, **(meta or {} if isinstance(meta, dict) else {"note": meta})})

            cur = await db.execute(
                """
                INSERT INTO transactions
                    (user_id, kind, amount, currency, balance_after, meta)
                VALUES (?, ?, ?, 'TON', ?, ?)
                """,
                (from_user, TxKind.TRANSFER_OUT.value, -amount, s_new, meta_out),
            )
            out_id = cur.lastrowid
            cur = await db.execute(
                """
                INSERT INTO transactions
                    (user_id, kind, amount, currency, balance_after, meta)
                VALUES (?, ?, ?, 'TON', ?, ?)
                """,
                (to_user, TxKind.TRANSFER_IN.value, amount, r_new, meta_in),
            )
            in_id = cur.lastrowid
            await db.commit()
            return (
                LedgerResult(s_new, s_usdt, out_id),
                LedgerResult(r_new, r_usdt, in_id),
            )
        except Exception:
            try:
                await db.execute("ROLLBACK")
            except Exception:
                pass
            raise


async def swap_ton_to_usdt(
    user_id: int,
    ton_amount: float,
    usdt_amount: float,
    *,
    rate: float,
    fee_ton: float,
) -> LedgerResult:
    if ton_amount <= 0 or usdt_amount <= 0:
        raise InvalidAmount("swap amounts must be positive")
    return await apply_entry(
        user_id,
        kind=TxKind.SWAP_TON_OUT,
        ton_delta=-ton_amount,
        usdt_delta=usdt_amount,
        meta={"rate": rate, "fee_ton": fee_ton, "usdt_received": usdt_amount, "pair": "TON/USDT"},
    )


async def swap_usdt_to_ton(
    user_id: int,
    usdt_amount: float,
    ton_amount: float,
    *,
    rate: float,
    fee_usdt: float,
) -> LedgerResult:
    if ton_amount <= 0 or usdt_amount <= 0:
        raise InvalidAmount("swap amounts must be positive")
    return await apply_entry(
        user_id,
        kind=TxKind.SWAP_USDT_OUT,
        ton_delta=ton_amount,
        usdt_delta=-usdt_amount,
        meta={"rate": rate, "fee_usdt": fee_usdt, "ton_received": ton_amount, "pair": "USDT/TON"},
    )


async def credit_deposit(
    user_id: int,
    amount: float,
    *,
    tx_hash: str,
    network: str = "TON",
) -> LedgerResult:
    """
    Credit deposit with chain tx hash as idempotency key.
    Also records a requests row as approved.
    """
    if amount <= 0:
        raise InvalidAmount("deposit must be positive")
    if not tx_hash:
        raise InvalidAmount("tx_hash required")

    key = f"deposit:{tx_hash}"
    result = await credit_ton(
        user_id,
        amount,
        kind=TxKind.DEPOSIT,
        meta={"tx_hash": tx_hash, "network": network},
        idempotency_key=key,
    )
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO requests (user_id, req_type, amount, network, tx_hash, status)
            VALUES (?, 'deposit', ?, ?, ?, 'approved')
            """,
            (user_id, amount, network, tx_hash),
        )
        await db.commit()
    return result


async def hold_withdraw(
    user_id: int,
    amount: float,
    *,
    address: str,
) -> tuple[LedgerResult, int]:
    """Debit balance immediately and create pending withdraw request. Returns (result, request_id)."""
    result = await debit_ton(
        user_id,
        amount,
        kind=TxKind.WITHDRAW_HOLD,
        meta={"address": address, "status": "pending"},
    )
    async with get_db() as db:
        cur = await db.execute(
            """
            INSERT INTO requests (user_id, req_type, amount, address, status)
            VALUES (?, 'withdraw', ?, ?, 'pending')
            """,
            (user_id, amount, address),
        )
        await db.commit()
        req_id = cur.lastrowid
    return result, req_id


async def complete_withdraw(request_id: int, *, tx_hash: str | None = None) -> None:
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT user_id, amount, status FROM requests WHERE request_id = ?",
            (request_id,),
        )
        row = await cur.fetchone()
        if not row:
            await db.execute("ROLLBACK")
            raise BusinessRuleError("request not found")
        if row["status"] != "pending":
            await db.execute("ROLLBACK")
            raise BusinessRuleError(f"request status is {row['status']}")

        await db.execute(
            "UPDATE requests SET status = 'completed', tx_hash = ? WHERE request_id = ?",
            (tx_hash or f"manual_{request_id}", request_id),
        )
        # Balance already deducted on hold — log completion only
        await db.execute(
            """
            INSERT INTO transactions
                (user_id, kind, amount, currency, balance_after, meta, idempotency_key)
            VALUES (
                ?, ?, 0, 'TON',
                (SELECT balance FROM users WHERE user_id = ?),
                ?, ?
            )
            """,
            (
                row["user_id"],
                TxKind.WITHDRAW_COMPLETE.value,
                row["user_id"],
                _meta_json({"request_id": request_id, "amount": row["amount"], "tx_hash": tx_hash}),
                f"withdraw_complete:{request_id}",
            ),
        )
        await db.commit()


async def reject_withdraw(request_id: int) -> LedgerResult:
    """Refund held amount back to user."""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT user_id, amount, status FROM requests WHERE request_id = ?",
            (request_id,),
        )
        row = await cur.fetchone()
        if not row:
            await db.execute("ROLLBACK")
            raise BusinessRuleError("request not found")
        if row["status"] != "pending":
            await db.execute("ROLLBACK")
            raise BusinessRuleError(f"request status is {row['status']}")

        user_id = int(row["user_id"])
        amount = float(row["amount"])
        await db.execute(
            "UPDATE requests SET status = 'rejected' WHERE request_id = ?",
            (request_id,),
        )
        await db.commit()

    return await credit_ton(
        user_id,
        amount,
        kind=TxKind.WITHDRAW_RELEASE,
        meta={"request_id": request_id},
        idempotency_key=f"withdraw_release:{request_id}",
    )


async def create_stake(
    user_id: int,
    amount: float,
    *,
    days: int,
    apy: float,
) -> tuple[LedgerResult, int]:
    end_date = datetime.utcnow() + timedelta(days=days)
    result = await debit_ton(
        user_id,
        amount,
        kind=TxKind.STAKE_LOCK,
        meta={"days": days, "apy": apy, "end_date": end_date.isoformat()},
    )
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO stakes (user_id, amount, apy, end_date, status) VALUES (?, ?, ?, ?, 'active')",
            (user_id, amount, apy, end_date.isoformat()),
        )
        await db.commit()
        stake_id = cur.lastrowid
    return result, stake_id


async def grant_loan(user_id: int, amount: float = 20.0, repay: float = 24.0) -> LedgerResult:
    ton, _ = await get_balances(user_id)
    async with get_db() as db:
        cur = await db.execute(
            "SELECT loan_amount FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        if not row:
            raise UserNotFound(str(user_id))
        if float(row["loan_amount"] or 0) > 0:
            raise BusinessRuleError("loan_exists")

    return await credit_ton(
        user_id,
        amount,
        kind=TxKind.LOAN_CREDIT,
        meta={"repay": repay},
        extra_user_updates={"loan_amount": repay},
    )




async def list_user_stakes(user_id: int, *, include_closed: bool = False) -> list[dict]:
    async with get_db() as db:
        if include_closed:
            cur = await db.execute(
                """
                SELECT id, amount, apy, end_date, status, created_at, unlocked_at
                FROM stakes WHERE user_id = ? ORDER BY id DESC
                """,
                (user_id,),
            )
        else:
            cur = await db.execute(
                """
                SELECT id, amount, apy, end_date, status, created_at, unlocked_at
                FROM stakes WHERE user_id = ? AND status = 'active' ORDER BY id DESC
                """,
                (user_id,),
            )
        return [dict(r) for r in await cur.fetchall()]


def _parse_end(end_date: str | None) -> datetime:
    if not end_date:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(str(end_date).replace("Z", ""))
    except Exception:
        return datetime.utcnow()


def compute_stake_reward(amount: float, apy: float, days: float) -> float:
    """Simple pro-rata APY reward for the locked period (not compound)."""
    if amount <= 0 or apy <= 0 or days <= 0:
        return 0.0
    return round(amount * (apy / 100.0) * (days / 365.0), 8)


async def unlock_stake(user_id: int, stake_id: int, *, force: bool = False) -> tuple[LedgerResult, float, float]:
    """
    Unlock a matured stake: return principal + reward via ledger.
    Returns (ledger_result, principal, reward).
    """
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT id, user_id, amount, apy, end_date, status FROM stakes WHERE id = ?",
            (stake_id,),
        )
        row = await cur.fetchone()
        if not row:
            await db.execute("ROLLBACK")
            raise BusinessRuleError("stake_not_found")
        if int(row["user_id"]) != int(user_id):
            await db.execute("ROLLBACK")
            raise BusinessRuleError("stake_not_owned")
        if (row["status"] or "active") != "active":
            await db.execute("ROLLBACK")
            raise BusinessRuleError("stake_already_closed")

        end_dt = _parse_end(row["end_date"])
        now = datetime.utcnow()
        if not force and now < end_dt:
            await db.execute("ROLLBACK")
            raise BusinessRuleError(f"stake_not_matured:{end_dt.isoformat()}")

        principal = float(row["amount"] or 0)
        apy = float(row["apy"] or 0)
        # Estimate lock days from APY plan metadata; fallback 30
        days = max((end_dt - (now - timedelta(days=30))).days, 1)
        # Better: derive from created if present — use apy plan inverse not needed;
        # reward for full planned period based on end - now if forced early = 0 extra
        if force and now < end_dt:
            reward = 0.0
        else:
            # Use full lock length encoded by comparing end to a synthetic start
            # Prefer meta days from typical plans
            if apy >= 20:
                plan_days = 90
            elif apy >= 10:
                plan_days = 30
            else:
                plan_days = 7
            reward = compute_stake_reward(principal, apy, plan_days)

        await db.execute(
            "UPDATE stakes SET status = 'unlocked', unlocked_at = ? WHERE id = ?",
            (now.isoformat(), stake_id),
        )
        await db.commit()

    total = principal + reward
    # Credit principal as unlock, reward as separate kinds when reward > 0
    if reward > 0:
        await credit_ton(
            user_id,
            principal,
            kind=TxKind.STAKE_UNLOCK,
            meta={"stake_id": stake_id, "principal": principal},
            idempotency_key=f"stake_unlock:{stake_id}",
        )
        result = await credit_ton(
            user_id,
            reward,
            kind=TxKind.STAKE_REWARD,
            meta={"stake_id": stake_id, "reward": reward, "apy": apy},
            idempotency_key=f"stake_reward:{stake_id}",
        )
    else:
        result = await credit_ton(
            user_id,
            principal,
            kind=TxKind.STAKE_UNLOCK,
            meta={"stake_id": stake_id, "principal": principal, "reward": 0},
            idempotency_key=f"stake_unlock:{stake_id}",
        )
    return result, principal, reward


async def repay_loan(user_id: int) -> LedgerResult:
    """Debit outstanding loan_amount and clear loan flag."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT loan_amount, balance FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise UserNotFound(str(user_id))
        due = float(row["loan_amount"] or 0)
        if due <= 0:
            raise BusinessRuleError("no_active_loan")

    result = await debit_ton(
        user_id,
        due,
        kind=TxKind.LOAN_REPAY,
        meta={"repaid": due},
        idempotency_key=f"loan_repay:{user_id}:{due}",
        extra_user_updates={"loan_amount": 0.0},
    )
    return result


async def list_transactions(
    user_id: int,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT id, kind, amount, currency, balance_after, meta, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def start_prop_challenge(
    user_id: int,
    *,
    plan_size: float,
    fee: float,
    profit_share: float = 0.8,
) -> tuple[LedgerResult, float]:
    """Charge fee from real TON and open/reset prop virtual account."""
    if fee <= 0 or plan_size <= 0:
        raise InvalidAmount("invalid prop plan")
    if profit_share <= 0 or profit_share > 1:
        profit_share = 0.8

    async with get_db() as db:
        cur = await db.execute(
            "SELECT status FROM prop_accounts WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row and str(row["status"] or "") == "active":
            raise BusinessRuleError("prop_already_active")

    result = await debit_ton(
        user_id,
        fee,
        kind=TxKind.PROP_FEE,
        meta={"plan_size": plan_size, "fee": fee},
        idempotency_key=None,
    )
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO prop_accounts (
                user_id, plan_size, virtual_balance, status,
                fee_paid, peak_balance, trades_count, profit_share,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'active', ?, ?, 0, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                plan_size = excluded.plan_size,
                virtual_balance = excluded.virtual_balance,
                status = 'active',
                fee_paid = excluded.fee_paid,
                peak_balance = excluded.peak_balance,
                trades_count = 0,
                profit_share = excluded.profit_share,
                created_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, plan_size, plan_size, fee, plan_size, profit_share),
        )
        await db.commit()
    return result, plan_size


async def get_prop_account(user_id: int) -> dict | None:
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT user_id, plan_size, virtual_balance, status,
                   fee_paid, peak_balance, trades_count, profit_share,
                   created_at, updated_at
            FROM prop_accounts WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_prop_trades(user_id: int, *, limit: int = 20) -> list[dict]:
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT id, direction, amount, entry_price, exit_price, profit, won,
                   virtual_balance, status_after, created_at
            FROM prop_trades WHERE user_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]


async def prop_virtual_trade(
    user_id: int,
    *,
    direction: str,
    amount: float,
    won: bool,
    entry_price: float | None = None,
    exit_price: float | None = None,
    payout_rate: float = 1.8,
) -> dict:
    """Apply virtual prop trade; enforce target / max drawdown."""
    direction = (direction or "").strip().lower()
    if direction not in ("up", "down"):
        raise BusinessRuleError("invalid_direction")
    try:
        amount = float(amount)
    except (TypeError, ValueError) as exc:
        raise InvalidAmount("invalid_amount") from exc
    if amount < 50:
        raise InvalidAmount("min_virtual_stake_50")

    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """
            SELECT plan_size, virtual_balance, status, peak_balance, trades_count, profit_share
            FROM prop_accounts WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            await db.execute("ROLLBACK")
            raise BusinessRuleError("no_prop_account")
        if row["status"] != "active":
            await db.execute("ROLLBACK")
            raise BusinessRuleError("prop_not_active")

        plan = float(row["plan_size"] or 0)
        bal = float(row["virtual_balance"] or 0)
        peak = float(row["peak_balance"] or plan or bal)
        trades = int(row["trades_count"] or 0)
        share = float(row["profit_share"] or 0.8)

        if amount > bal:
            await db.execute("ROLLBACK")
            raise InsufficientBalance("insufficient_virtual_balance")
        # risk limit: max 10% of current virtual equity per trade
        max_stake = max(50.0, round(bal * 0.10, 8))
        if amount > max_stake:
            await db.execute("ROLLBACK")
            raise BusinessRuleError("max_stake_10pct")

        if won:
            profit = round(amount * (payout_rate - 1.0), 8)
            new_bal = round(bal + profit, 8)
        else:
            profit = -amount
            new_bal = round(bal - amount, 8)

        if new_bal > peak:
            peak = new_bal

        dd_floor = plan * 0.90
        target = plan * 1.10
        status = "active"
        if new_bal < dd_floor:
            status = "failed"
        elif new_bal >= target:
            status = "passed"

        await db.execute(
            """
            UPDATE prop_accounts
            SET virtual_balance = ?, status = ?, peak_balance = ?,
                trades_count = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (new_bal, status, peak, trades + 1, user_id),
        )
        await db.execute(
            """
            INSERT INTO prop_trades (
                user_id, direction, amount, entry_price, exit_price,
                profit, won, virtual_balance, status_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, direction, amount, entry_price, exit_price,
                profit, 1 if won else 0, new_bal, status,
            ),
        )
        await db.commit()
        return {
            "plan_size": plan,
            "virtual_balance": new_bal,
            "status": status,
            "profit": profit,
            "won": won,
            "dd_floor": dd_floor,
            "target": target,
            "peak_balance": peak,
            "trades_count": trades + 1,
            "profit_share": share,
            "claimable": round(max(0.0, (new_bal - plan) * share), 8) if status == "passed" else 0.0,
        }


async def claim_prop_reward(user_id: int) -> dict:
    """Pay profit share to real TON balance when challenge PASSED."""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """
            SELECT plan_size, virtual_balance, status, profit_share, fee_paid
            FROM prop_accounts WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            await db.execute("ROLLBACK")
            raise BusinessRuleError("no_prop_account")
        if row["status"] == "paid":
            await db.execute("ROLLBACK")
            raise BusinessRuleError("already_claimed")
        if row["status"] != "passed":
            await db.execute("ROLLBACK")
            raise BusinessRuleError("challenge_not_passed")

        plan = float(row["plan_size"] or 0)
        bal = float(row["virtual_balance"] or 0)
        share = float(row["profit_share"] or 0.8)
        gross = max(0.0, bal - plan)
        reward = round(gross * share, 8)
        if reward <= 0:
            await db.execute("ROLLBACK")
            raise BusinessRuleError("no_profit_to_claim")

        await db.execute(
            """
            UPDATE prop_accounts
            SET status = 'paid', updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND status = 'passed'
            """,
            (user_id,),
        )
        await db.commit()

    result = await credit_ton(
        user_id,
        reward,
        kind=TxKind.PROP_REWARD,
        meta={
            "plan_size": plan,
            "virtual_balance": bal,
            "gross_profit": gross,
            "profit_share": share,
            "reward": reward,
        },
        idempotency_key=f"prop_reward:{user_id}:{plan}:{int(bal)}",
    )
    return {
        "reward": reward,
        "gross_profit": gross,
        "profit_share": share,
        "plan_size": plan,
        "status": "paid",
        "balance_ton": result.ton_balance,
    }
