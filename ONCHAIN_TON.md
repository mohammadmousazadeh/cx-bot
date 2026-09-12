# Real TON deposit & withdraw

## Deposit
1. User sends TON to `EXCHANGE_WALLET` / `PAYMENT_WALLET`
2. **Memo/comment must be** `cx_<telegram_user_id>` (example: `cx_5044524748`)
3. Worker every 30s (or manual check) credits Ledger via `tx_hash` idempotency

## Withdraw
1. User requests withdraw → balance held (`withdraw_hold`)
2. Admin presses settle → hot wallet sends real TON
3. `complete_withdraw` stores real tx hash

## Required env
```env
EXCHANGE_WALLET=UQ...
PAYMENT_WALLET=UQ...
TONCENTER_API_KEY=  # recommended
MIN_DEPOSIT_TON=1
DEPOSIT_WORKER_ENABLED=true
DEPOSIT_WORKER_INTERVAL=30
HOT_WALLET_MNEMONIC=word1 word2 ... word24
WITHDRAW_ONCHAIN_ENABLED=true
```

## Security
- Never commit mnemonic
- Keep only operational balance on hot wallet
- Cold storage for treasury
