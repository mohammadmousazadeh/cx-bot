"""Database connection helpers."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

from bot.config import settings

# Schema version for future migrations
SCHEMA_VERSION = 5


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """Async context manager for a SQLite connection with row factory."""
    db = await aiosqlite.connect(settings.db_name)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA foreign_keys=ON;")
        yield db
    finally:
        await db.close()


async def init_db() -> None:
    """Create tables and apply lightweight column migrations."""
    async with get_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                phone TEXT,
                email TEXT,
                lang TEXT DEFAULT 'fa',
                balance REAL DEFAULT 0.0,
                usdt_balance REAL DEFAULT 0.0,
                referrer_id INTEGER,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                kyc_level INTEGER DEFAULT 0,
                last_bonus_date TIMESTAMP,
                is_vip INTEGER DEFAULT 0,
                vip_expire_date TIMESTAMP,
                loan_amount REAL DEFAULT 0.0,
                security_pin TEXT,
                cooldown_until TIMESTAMP,
                whitelist_address TEXT,
                task_channel INTEGER DEFAULT 0,
                task_invite INTEGER DEFAULT 0,
                task_youtube INTEGER DEFAULT 0
            )
            """
        )

        # Backward-compatible column adds (ignore if already exists)
        columns_to_add = [
            ("email", "TEXT"),
            ("usdt_balance", "REAL DEFAULT 0.0"),
            ("security_pin", "TEXT"),
            ("cooldown_until", "TIMESTAMP"),
            ("whitelist_address", "TEXT"),
        ]
        for col, col_type in columns_to_add:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
            except Exception:
                pass

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS security_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS support_tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                req_type TEXT,
                amount REAL,
                network TEXT,
                address TEXT,
                tx_hash TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS prop_accounts (
                user_id INTEGER PRIMARY KEY,
                plan_size REAL,
                virtual_balance REAL,
                status TEXT DEFAULT 'active',
                fee_paid REAL DEFAULT 0,
                peak_balance REAL DEFAULT 0,
                trades_count INTEGER DEFAULT 0,
                profit_share REAL DEFAULT 0.8,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS prop_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                amount REAL NOT NULL,
                entry_price REAL,
                exit_price REAL,
                profit REAL,
                won INTEGER,
                virtual_balance REAL,
                status_after TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for _ddl in (
            "ALTER TABLE prop_accounts ADD COLUMN fee_paid REAL DEFAULT 0",
            "ALTER TABLE prop_accounts ADD COLUMN peak_balance REAL DEFAULT 0",
            "ALTER TABLE prop_accounts ADD COLUMN trades_count INTEGER DEFAULT 0",
            "ALTER TABLE prop_accounts ADD COLUMN profit_share REAL DEFAULT 0.8",
            "ALTER TABLE prop_accounts ADD COLUMN created_at TIMESTAMP",
            "ALTER TABLE prop_accounts ADD COLUMN updated_at TIMESTAMP",
        ):
            try:
                await db.execute(_ddl)
            except Exception:
                pass
        await db.execute(
            """
            
            CREATE TABLE IF NOT EXISTS kyc_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                email TEXT,
                passport_file_id TEXT,
                selfie_file_id TEXT,
                status TEXT DEFAULT 'pending',
                admin_note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TIMESTAMP
            )
            """
        )
        for _ddl in (
            "ALTER TABLE users ADD COLUMN referral_code TEXT",
            "ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0",
        ):
            try:
                await db.execute(_ddl)
            except Exception:
                pass
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS stakes (

                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                apy REAL NOT NULL,
                end_date TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                unlocked_at TIMESTAMP
            )
            """
        )
        try:
            await db.execute("ALTER TABLE stakes ADD COLUMN status TEXT DEFAULT 'active'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE stakes ADD COLUMN created_at TIMESTAMP")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE stakes ADD COLUMN unlocked_at TIMESTAMP")
        except Exception:
            pass
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_stakes_user_status ON stakes(user_id, status)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS binary_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                amount REAL NOT NULL,
                payout_rate REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                duration_sec INTEGER NOT NULL,
                open_time TIMESTAMP NOT NULL,
                expire_time TIMESTAMP NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                profit REAL DEFAULT 0,
                client_request_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_binary_trades_user
            ON binary_trades(user_id, id DESC)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_binary_trades_open
            ON binary_trades(status, expire_time)
            """
        )
        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_binary_client_req
            ON binary_trades(user_id, client_request_id)
            WHERE client_request_id IS NOT NULL
            """
        )

        # Financial ledger — single source of truth for money movements

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS sniper_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                amount REAL NOT NULL,
                payout_rate REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                open_time TIMESTAMP NOT NULL,
                settle_time TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'open',
                profit REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sniper_user ON sniper_rounds(user_id, id DESC)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'TON',
                balance_after REAL,
                meta TEXT,
                idempotency_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Migration for existing DBs created before idempotency_key
        try:
            await db.execute(
                "ALTER TABLE transactions ADD COLUMN idempotency_key TEXT"
            )
        except Exception:
            pass
        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_idempotency
            ON transactions(idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_transactions_user_id
            ON transactions(user_id, id DESC)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        await db.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        await db.commit()
