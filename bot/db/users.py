"""User-related database operations."""
from __future__ import annotations

from typing import Any, Optional, Tuple

from bot.db.connection import get_db

# Tuple layout kept compatible with existing handlers:
# (lang, kyc_level, is_vip, loan_amount, balance, phone, security_pin,
#  cooldown_until, whitelist_address, usdt_balance, email)
UserRow = Tuple[
    str, int, int, float, float, Optional[str], Optional[str],
    Optional[str], Optional[str], float, Optional[str],
]

_EMPTY_USER: UserRow = ("fa", 0, 0, 0.0, 0.0, None, None, None, None, 0.0, None)


async def get_user_data(user_id: int) -> UserRow:
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT lang, kyc_level, is_vip, loan_amount, balance, phone, security_pin,
                   cooldown_until, whitelist_address, usdt_balance, email
            FROM users WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return _EMPTY_USER
        return (
            row["lang"] or "fa",
            int(row["kyc_level"] or 0),
            int(row["is_vip"] or 0),
            float(row["loan_amount"] or 0.0),
            float(row["balance"] or 0.0),
            row["phone"],
            row["security_pin"],
            row["cooldown_until"],
            row["whitelist_address"],
            float(row["usdt_balance"] or 0.0),
            row["email"],
        )


async def log_security_event(user_id: int, action: str) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO security_logs (user_id, action) VALUES (?, ?)",
            (user_id, action),
        )
        await db.commit()


async def ensure_user(
    user_id: int,
    *,
    referrer_id: int | None = None,
    kyc_level: int = 0,
    bonus: float = 0.0,
) -> bool:
    """
    Insert user if missing. Returns True if newly created.
    """
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
        )
        exists = await cursor.fetchone()
        if exists:
            return False
        await db.execute(
            """
            INSERT INTO users (user_id, referrer_id, kyc_level, balance)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, referrer_id, kyc_level, bonus),
        )
        if bonus:
            await db.execute(
                """
                INSERT INTO transactions (user_id, kind, amount, currency, balance_after, meta)
                VALUES (?, 'welcome_bonus', ?, 'TON', ?, ?)
                """,
                (user_id, bonus, bonus, "signup"),
            )
        await db.commit()
        return True


async def update_balance(
    user_id: int,
    delta: float,
    *,
    kind: str,
    currency: str = "TON",
    meta: str | None = None,
) -> float:
    """Deprecated wrapper — prefer bot.db.ledger.apply_entry."""
    from bot.db.ledger import apply_entry, TxKind
    ton_delta = delta if currency.upper() == "TON" else 0.0
    usdt_delta = delta if currency.upper() == "USDT" else 0.0
    res = await apply_entry(
        user_id,
        kind=kind,
        ton_delta=ton_delta,
        usdt_delta=usdt_delta,
        meta=meta,
    )
    return res.ton_balance if currency.upper() == "TON" else res.usdt_balance


async def set_user_fields(user_id: int, **fields: Any) -> None:
    if not fields:
        return
    # balance / usdt_balance must go through ledger — not allowed here
    allowed = {
        "phone", "email", "lang", "kyc_level",
        "is_vip", "vip_expire_date", "loan_amount", "security_pin",
        "cooldown_until", "whitelist_address", "task_channel",
        "task_invite", "task_youtube", "referrer_id", "last_bonus_date",
    }
    sets = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"disallowed field: {key}")
        sets.append(f"{key} = ?")
        values.append(value)
    values.append(user_id)
    sql = f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?"
    async with get_db() as db:
        await db.execute(sql, values)
        await db.commit()
