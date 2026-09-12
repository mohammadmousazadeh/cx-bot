"""
Real TON on-chain deposit detection and withdrawal sending.

Env:
  EXCHANGE_WALLET / PAYMENT_WALLET  — deposit address (receive)
  HOT_WALLET_MNEMONIC               — 24 words for withdraw hot wallet (optional until real send)
  TONCENTER_API_KEY                 — recommended for rate limits
  TONCENTER_BASE                    — default https://toncenter.com/api/v2
  MIN_DEPOSIT_TON                   — minimum credit amount (default 1)
  DEPOSIT_LOOKBACK                  — tx limit per scan (default 50)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

from bot.config import settings

logger = logging.getLogger("cx.ton_chain")

COMMENT_RE = re.compile(r"^(?:cx[_-]?)?(\d{5,15})$", re.IGNORECASE)


@dataclass
class ChainTx:
    tx_hash: str
    value_ton: float
    comment: str
    lt: str | None = None
    utime: int | None = None


def _base() -> str:
    return (getattr(settings, "toncenter_base", None) or "https://toncenter.com/api/v2").rstrip("/")


def _api_key() -> str:
    return (getattr(settings, "toncenter_api_key", None) or "").strip()


def _headers() -> dict[str, str]:
    key = _api_key()
    return {"X-API-Key": key} if key else {}


def _params(**extra: Any) -> dict[str, Any]:
    p = dict(extra)
    key = _api_key()
    if key:
        p["api_key"] = key
    return p


def parse_deposit_user_id(comment: str) -> int | None:
    """Accept: cx_123, CX-123, 123"""
    raw = (comment or "").strip()
    m = COMMENT_RE.match(raw)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def deposit_comment_for_user(user_id: int) -> str:
    return f"cx_{user_id}"


async def fetch_wallet_transactions(
    address: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    url = f"{_base()}/getTransactions"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
        async with session.get(
            url, params=_params(address=address, limit=limit, archival="true")
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"toncenter HTTP {resp.status}: {text[:200]}")
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"toncenter error: {data}")
            return list(data.get("result") or [])


def _extract_inbound(tx: dict[str, Any]) -> ChainTx | None:
    in_msg = tx.get("in_msg") or {}
    value = int(in_msg.get("value") or 0)
    if value <= 0:
        return None
    # Prefer decoded comment; toncenter may put it in message or msg_data
    comment = (in_msg.get("message") or "").strip()
    if not comment:
        msg_data = in_msg.get("msg_data") or {}
        if isinstance(msg_data, dict):
            comment = (msg_data.get("text") or msg_data.get("message") or "").strip()
    tx_id = tx.get("transaction_id") or {}
    tx_hash = str(tx_id.get("hash") or tx.get("hash") or "").strip()
    if not tx_hash:
        # fallback unique key
        tx_hash = f"lt_{tx_id.get('lt')}_{tx.get('utime')}"
    return ChainTx(
        tx_hash=tx_hash,
        value_ton=value / 1_000_000_000,
        comment=comment,
        lt=str(tx_id.get("lt")) if tx_id.get("lt") is not None else None,
        utime=tx.get("utime"),
    )


async def list_inbound_deposits(
    *,
    limit: int | None = None,
) -> list[tuple[int, ChainTx]]:
    """
    Returns list of (user_id, ChainTx) for recognized deposit comments.
    """
    wallet = settings.payment_wallet or settings.exchange_wallet
    if not wallet:
        return []
    limit = limit or int(getattr(settings, "deposit_lookback", 50) or 50)
    txs = await fetch_wallet_transactions(wallet, limit=limit)
    found: list[tuple[int, ChainTx]] = []
    for raw in txs:
        parsed = _extract_inbound(raw)
        if not parsed:
            continue
        uid = parse_deposit_user_id(parsed.comment)
        if uid is None:
            continue
        found.append((uid, parsed))
    return found


async def credit_new_deposits(*, min_amount: float | None = None) -> list[dict[str, Any]]:
    """
    Scan chain and credit any new deposits via ledger (idempotent on tx_hash).
    """
    from bot.db.ledger import DuplicateTransaction, InvalidAmount, credit_deposit

    min_amount = float(
        min_amount
        if min_amount is not None
        else getattr(settings, "min_deposit_ton", 1.0) or 1.0
    )
    results: list[dict[str, Any]] = []
    try:
        items = await list_inbound_deposits()
    except Exception as exc:
        logger.exception("deposit scan failed")
        return [{"ok": False, "error": str(exc)}]

    for user_id, tx in items:
        if tx.value_ton < min_amount:
            results.append(
                {
                    "ok": False,
                    "skipped": "below_min",
                    "user_id": user_id,
                    "amount": tx.value_ton,
                    "tx_hash": tx.tx_hash,
                }
            )
            continue
        try:
            ledger = await credit_deposit(
                user_id,
                tx.value_ton,
                tx_hash=tx.tx_hash,
                network="TON",
            )
            results.append(
                {
                    "ok": True,
                    "user_id": user_id,
                    "amount": tx.value_ton,
                    "tx_hash": tx.tx_hash,
                    "balance_ton": ledger.ton_balance,
                }
            )
            logger.info(
                "Deposit credited user=%s amount=%s tx=%s",
                user_id,
                tx.value_ton,
                tx.tx_hash,
            )
        except DuplicateTransaction:
            results.append(
                {
                    "ok": True,
                    "duplicate": True,
                    "user_id": user_id,
                    "tx_hash": tx.tx_hash,
                }
            )
        except InvalidAmount as exc:
            results.append({"ok": False, "error": str(exc), "tx_hash": tx.tx_hash})
        except Exception as exc:
            logger.exception("credit deposit failed")
            results.append(
                {"ok": False, "error": str(exc), "user_id": user_id, "tx_hash": tx.tx_hash}
            )
    return results


async def check_user_deposit(user_id: int, *, min_amount: float = 0.0) -> float:
    """
    Scan and credit deposits for one user. Returns total newly credited TON.
    """
    credited = 0.0
    for row in await credit_new_deposits(min_amount=min_amount or None):
        if row.get("ok") and not row.get("duplicate") and row.get("user_id") == user_id:
            credited += float(row.get("amount") or 0)
    return credited


# ---------------------------------------------------------------------------
# Outbound transfer (real withdraw)
# ---------------------------------------------------------------------------

class TonSendError(Exception):
    pass


class TonWalletNotConfigured(TonSendError):
    pass


async def send_ton(
    to_address: str,
    amount_ton: float,
    *,
    comment: str = "",
) -> str:
    """
    Send TON from hot wallet. Returns tx hash / message hash string.

    Requires HOT_WALLET_MNEMONIC in env (24 words).
    Uses pytoniq LiteBalancer on mainnet.
    """
    mnemonic = (getattr(settings, "hot_wallet_mnemonic", None) or "").strip()
    if not mnemonic:
        raise TonWalletNotConfigured(
            "HOT_WALLET_MNEMONIC is not set — cannot send on-chain withdraw"
        )
    words = mnemonic.split()
    if len(words) not in (12, 24):
        raise TonWalletNotConfigured("HOT_WALLET_MNEMONIC must be 12 or 24 words")

    if amount_ton <= 0:
        raise TonSendError("amount must be positive")
    to_address = (to_address or "").strip()
    if not to_address.startswith(("EQ", "UQ", "0:")):
        raise TonSendError("invalid destination address")

    try:
        from pytoniq import LiteBalancer, WalletV4R2
    except ImportError as exc:
        raise TonSendError(
            "pytoniq is not installed. Add pytoniq to requirements and redeploy."
        ) from exc

    client = LiteBalancer.from_mainnet_config(1)
    try:
        await client.start_up()
        wallet = await WalletV4R2.from_mnemonic(client, words)
        nanotons = int(amount_ton * 1_000_000_000)
        # transfer returns list of messages / result depending on version
        result = await wallet.transfer(
            destination=to_address,
            amount=nanotons,
            body=comment or "",
        )
        # Best-effort extract hash
        tx_hash = ""
        if isinstance(result, (list, tuple)) and result:
            first = result[0]
            tx_hash = str(getattr(first, "hash", None) or first)
        elif result is not None:
            tx_hash = str(getattr(result, "hash", None) or result)
        if not tx_hash:
            tx_hash = f"sent:{to_address}:{nanotons}"
        logger.info("TON sent amount=%s to=%s hash=%s", amount_ton, to_address, tx_hash)
        return tx_hash
    except TonSendError:
        raise
    except Exception as exc:
        logger.exception("send_ton failed")
        raise TonSendError(str(exc)) from exc
    finally:
        try:
            await client.close_all()
        except Exception:
            pass


def hot_wallet_configured() -> bool:
    m = (getattr(settings, "hot_wallet_mnemonic", None) or "").strip()
    return len(m.split()) in (12, 24)
