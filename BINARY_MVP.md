# Binary Options MVP

## Endpoints
- GET  /api/binary/config
- GET  /api/binary/price?symbol=BTCUSDT
- POST /api/binary/open   (requires X-Telegram-Init-Data)
- GET  /api/binary/history
- POST /api/binary/settle-due

## Open body
```json
{
  "direction": "up",
  "amount": 5,
  "duration_sec": 60,
  "symbol": "BTCUSDT",
  "client_request_id": "optional-unique-id"
}
```

## Flow
1. Debit ledger `binary_bet`
2. Insert `binary_trades` status=open
3. After expire: POST /api/binary/settle-due → win credits `binary_win`

## UI
`web/public/binary.html` calls `/api/binary/open` via CXAuth.

## Auto settler worker

Runs inside `python -m bot.main`:

```env
BINARY_SETTLER_ENABLED=true
BINARY_SETTLE_INTERVAL=5
```

Every N seconds:
1. Finds `binary_trades` with status=open and expire_time <= now
2. Settles via market price
3. Credits `binary_win` on wins
4. Notifies user in Telegram
