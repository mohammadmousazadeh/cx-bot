-- CX Exchange — Postgres schema (cutover target)
-- Apply manually when migrating off SQLite. Do not set DATABASE_URL alone.

CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    phone TEXT,
    email TEXT,
    lang TEXT DEFAULT 'fa',
    balance DOUBLE PRECISION DEFAULT 0,
    usdt_balance DOUBLE PRECISION DEFAULT 0,
    btc_balance DOUBLE PRECISION DEFAULT 0,
    eth_balance DOUBLE PRECISION DEFAULT 0,
    referrer_id BIGINT,
    join_date TIMESTAMPTZ DEFAULT NOW(),
    kyc_level INTEGER DEFAULT 0,
    last_bonus_date TIMESTAMPTZ,
    is_vip INTEGER DEFAULT 0,
    vip_expire_date TIMESTAMPTZ,
    loan_amount DOUBLE PRECISION DEFAULT 0,
    security_pin TEXT,
    cooldown_until TIMESTAMPTZ,
    whitelist_address TEXT,
    referral_code TEXT,
    referral_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    kind TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    currency TEXT,
    balance_after DOUBLE PRECISION,
    meta TEXT,
    idempotency_key TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id, id DESC);

CREATE TABLE IF NOT EXISTS requests (
    request_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    req_type TEXT,
    amount DOUBLE PRECISION,
    address TEXT,
    status TEXT,
    tx_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS binary_trades (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    symbol TEXT,
    direction TEXT,
    amount DOUBLE PRECISION,
    payout_rate DOUBLE PRECISION,
    entry_price DOUBLE PRECISION,
    exit_price DOUBLE PRECISION,
    status TEXT,
    profit DOUBLE PRECISION,
    duration_sec INTEGER,
    open_time TIMESTAMPTZ,
    expire_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Remaining tables: mirror SQLite init_db (stakes, prop, kyc_docs, security_logs, ...)
