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


async def ensure_referral_code(user_id: int) -> str:
    import secrets
    from bot.db.connection import get_db
    async with get_db() as db:
        cur = await db.execute("SELECT referral_code FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if row and row[0]:
            return str(row[0])
        for _ in range(8):
            code = "CX-" + secrets.token_hex(3).upper()
            try:
                await db.execute(
                    "UPDATE users SET referral_code = ? WHERE user_id = ? AND (referral_code IS NULL OR referral_code = '')",
                    (code, user_id),
                )
                await db.commit()
                cur2 = await db.execute("SELECT referral_code FROM users WHERE user_id = ?", (user_id,))
                r2 = await cur2.fetchone()
                if r2 and r2[0]:
                    return str(r2[0])
            except Exception:
                continue
        return "CX-%s" % user_id


async def apply_referral_code(user_id: int, code: str) -> tuple[bool, str]:
    from bot.db.connection import get_db
    code = (code or "").strip().upper()
    if not code:
        return False, "empty"
    async with get_db() as db:
        cur = await db.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return False, "no_user"
        if row[0]:
            return False, "already"
        cur = await db.execute(
            "SELECT user_id FROM users WHERE upper(COALESCE(referral_code,'')) = ? LIMIT 1",
            (code,),
        )
        ref = await cur.fetchone()
        if not ref:
            return False, "invalid"
        ref_id = int(ref[0])
        if ref_id == int(user_id):
            return False, "self"
        await db.execute("UPDATE users SET referrer_id = ? WHERE user_id = ?", (ref_id, user_id))
        await db.execute(
            "UPDATE users SET referral_count = COALESCE(referral_count, 0) + 1 WHERE user_id = ?",
            (ref_id,),
        )
        await db.commit()
        return True, "ok"


async def pay_referral_l2_reward(invitee_id: int) -> float:
    """Credit referrer when invitee reaches KYC L2. Returns amount paid (0 if none)."""
    from bot.config import settings
    from bot.db.connection import get_db
    from bot.db.ledger import TxKind, credit_ton

    reward = float(getattr(settings, "referral_l2_reward", 0) or 0)
    if reward <= 0:
        return 0.0
    async with get_db() as db:
        cur = await db.execute("SELECT referrer_id FROM users WHERE user_id = ?", (invitee_id,))
        row = await cur.fetchone()
    if not row or not row[0]:
        return 0.0
    ref_id = int(row[0])
    try:
        await credit_ton(
            ref_id,
            reward,
            kind=TxKind.MISSION_REWARD,
            meta={"type": "referral_l2", "invitee": invitee_id, "amount": reward},
            idempotency_key=f"referral_l2:{invitee_id}",
        )
        return reward
    except Exception:
        return 0.0



async def get_user_seen_version(user_id: int) -> str:
    async with get_db() as db:
        try:
            cur = await db.execute(
                "SELECT seen_app_version FROM users WHERE user_id=?",
                (user_id,),
            )
            row = await cur.fetchone()
            if row:
                return str(row[0] or "")
        except Exception:
            return ""
    return ""


async def mark_app_version_seen(user_id: int, version: str) -> None:
    async with get_db() as db:
        try:
            await db.execute(
                "UPDATE users SET seen_app_version=? WHERE user_id=?",
                (version, user_id),
            )
            await db.commit()
        except Exception:
            pass
