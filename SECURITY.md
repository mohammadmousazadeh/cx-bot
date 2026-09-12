# CX Security Notes

## Controls implemented
- Secrets only via environment (BOT_TOKEN, HOT_WALLET_MNEMONIC)
- Telegram WebApp initData HMAC validation
- Ledger atomic updates + idempotency keys for deposits
- Rate limits on API and sensitive handlers
- Emergency freeze / maintenance gates
- Finite-amount checks on ledger deltas
- Binary/Sniper reject when market price unavailable (no silent fallback)
- Predict settled against live price (15s); refund on oracle failure
- Daily bonus deterministic + 24h gate
- Auto-withdraw capped by AUTO_WITHDRAW_MAX
- Admin-only settlement callbacks

## Operator checklist
1. Never commit .env or mnemonics
2. Keep hot wallet balance low
3. Set WEBAPP_CORS_ORIGINS to exact Pages domain
4. Rotate BOT_TOKEN if leaked
5. Run: `python scripts/security_selftest.py`

## Residual risks
- SQLite single-node limits under extreme concurrency
- Toncenter availability for deposit detection
- Legal/compliance for binary/sniper products by jurisdiction
