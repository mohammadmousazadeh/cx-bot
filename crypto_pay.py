import os

import aiohttp
from dotenv import load_dotenv

load_dotenv()

# Prefer PAYMENT_WALLET, fall back to EXCHANGE_WALLET
WALLET_ADDRESS = os.getenv("PAYMENT_WALLET") or os.getenv("EXCHANGE_WALLET", "")


def generate_ton_payment(user_id: int, amount: float):
    if not WALLET_ADDRESS:
        raise RuntimeError("PAYMENT_WALLET / EXCHANGE_WALLET is not set in .env")

    comment = f"cx_{user_id}"
    nanotons = int(amount * 1_000_000_000)
    transfer_url = f"ton://transfer/{WALLET_ADDRESS}?amount={nanotons}&text={comment}"
    return {
        "address": WALLET_ADDRESS,
        "comment": comment,
        "amount": amount,
        "url": transfer_url,
    }


async def check_ton_transaction(user_id: int, expected_amount: float) -> bool:
    if not WALLET_ADDRESS:
        return False

    comment = f"cx_{user_id}"
    api_url = f"https://toncenter.com/api/v2/getTransactions?address={WALLET_ADDRESS}&limit=20"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as response:
                if response.status != 200:
                    return False
                data = await response.json()
                if not data.get("ok"):
                    return False

                transactions = data.get("result", [])
                for tx in transactions:
                    in_msg = tx.get("in_msg", {})
                    msg_comment = in_msg.get("message", "")
                    value = int(in_msg.get("value", 0)) / 1_000_000_000

                    if msg_comment.strip() == comment and value >= (expected_amount * 0.98):
                        return True
    except Exception:
        return False
    return False
