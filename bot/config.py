"""
Central configuration — all secrets from environment variables.
Never hardcode tokens or wallet addresses in source code.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _cors_list(value: str | None) -> List[str]:
    if not value or value.strip() == "*":
        return ["*"]
    return [x.strip() for x in value.split(",") if x.strip()]


@dataclass
class Settings:
    bot_token: str
    admin_id: int
    db_name: str
    exchange_wallet: str
    payment_wallet: str
    channel_id: str
    youtube_video_url: str
    maintenance_mode: bool
    emergency_freeze: bool
    webapp_base_url: str
    welcome_bonus_ton: float
    project_name: str
    # HTTP API + server
    api_enabled: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    webapp_init_max_age: int = 86400
    webapp_cors_origins: List[str] = field(default_factory=lambda: ["*"])
    webapp_api_public_url: str = ""
    # Telegram webhook (production)
    # If WEBHOOK_URL is set → webhook mode; otherwise long-polling
    webhook_url: str = ""
    webhook_path: str = "/telegram/webhook"
    webhook_secret: str = ""
    webhook_drop_pending: bool = True
    # Redis for multi-instance FSM (optional)
    redis_url: str = ""
    # Binary settler worker
    binary_settler_enabled: bool = True
    binary_settle_interval: float = 5.0
    # TON chain
    toncenter_api_key: str = ""
    toncenter_base: str = "https://toncenter.com/api/v2"
    hot_wallet_mnemonic: str = ""
    min_deposit_ton: float = 1.0
    deposit_lookback: int = 50
    deposit_worker_enabled: bool = True
    deposit_worker_interval: float = 30.0
    withdraw_onchain_enabled: bool = True
    auto_withdraw_enabled: bool = True
    auto_withdraw_max: float = 20.0
    withdraw_min_ton: float = 1.0
    withdraw_max_l0: float = 0.0
    withdraw_max_l1: float = 50.0
    withdraw_max_l2: float = 500.0
    withdraw_daily_max_l0: float = 0.0
    withdraw_daily_max_l1: float = 100.0
    withdraw_daily_max_l2: float = 1000.0
    backup_enabled: bool = True
    backup_interval_sec: float = 3600.0
    backup_dir: str = "backups"
    app_version: str = "1.0.0"
    app_changelog: str = ""
    database_url: str = ""  # optional postgres URL for future / external tools
    bot_username: str = ""
    referral_l1_reward: float = 1.0
    referral_l2_reward: float = 2.0
    withdraw_require_pin: bool = True
    withdraw_require_whitelist: bool = True
    pin_max_fails: int = 5


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN is missing. Copy .env.example to .env and set BOT_TOKEN."
        )

    admin_raw = os.getenv("ADMIN_ID", "0").strip()
    try:
        admin_id = int(admin_raw)
    except ValueError as exc:
        raise RuntimeError("ADMIN_ID must be a numeric Telegram user id") from exc

    exchange = os.getenv("EXCHANGE_WALLET", "").strip()
    payment = os.getenv("PAYMENT_WALLET", "").strip() or exchange

    webhook_url = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
    webhook_path = os.getenv("WEBHOOK_PATH", "/telegram/webhook").strip()
    if not webhook_path.startswith("/"):
        webhook_path = "/" + webhook_path

    webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if webhook_url and not webhook_secret:
        # Generate once per process if missing — better set permanently in .env
        webhook_secret = secrets.token_urlsafe(32)
        logger.warning(
            "WEBHOOK_SECRET was empty — generated ephemeral secret. "
            "Set WEBHOOK_SECRET in .env for stable deploys."
        )

    return Settings(
        bot_token=token,
        admin_id=admin_id,
        referral_l1_reward=float(os.getenv("REFERRAL_L1_REWARD", "1")),
        referral_l2_reward=float(os.getenv("REFERRAL_L2_REWARD", "2")),
        withdraw_require_pin=_bool(os.getenv("WITHDRAW_REQUIRE_PIN"), True),
        withdraw_require_whitelist=_bool(os.getenv("WITHDRAW_REQUIRE_WHITELIST"), True),
        pin_max_fails=int(os.getenv("PIN_MAX_FAILS", "5")),
        db_name=os.getenv("DB_NAME", "cx_database.db").strip(),
        exchange_wallet=exchange,
        payment_wallet=payment,
        channel_id=os.getenv("CHANNEL_ID", "@cx_trader").strip(),
        youtube_video_url=os.getenv(
            "YOUTUBE_VIDEO_URL", "https://youtube.com/shorts/5guC6GdalHc"
        ).strip(),
        maintenance_mode=_bool(os.getenv("MAINTENANCE_MODE"), False),
        emergency_freeze=_bool(os.getenv("EMERGENCY_FREEZE"), False),
        webapp_base_url=os.getenv(
            "WEBAPP_BASE_URL",
            "https://restless-pond-5c7c.bonitobonit88.workers.dev",
        ).rstrip("/"),
        welcome_bonus_ton=float(os.getenv("WELCOME_BONUS_TON", "5")),
        project_name=os.getenv("PROJECT_NAME", "CX"),
        api_enabled=_bool(os.getenv("API_ENABLED"), True),
        api_host=os.getenv("API_HOST", "0.0.0.0").strip(),
        api_port=int(os.getenv("API_PORT", "8080")),
        webapp_init_max_age=int(os.getenv("WEBAPP_INIT_MAX_AGE", "3600")),
        webapp_cors_origins=_cors_list(os.getenv("WEBAPP_CORS_ORIGINS", "*")),
        webapp_api_public_url=os.getenv("WEBAPP_API_PUBLIC_URL", "").rstrip("/"),
        webhook_url=webhook_url,
        webhook_path=webhook_path,
        webhook_secret=webhook_secret,
        webhook_drop_pending=_bool(os.getenv("WEBHOOK_DROP_PENDING"), True),
        redis_url=os.getenv("REDIS_URL", "").strip(),
        binary_settler_enabled=_bool(os.getenv("BINARY_SETTLER_ENABLED"), True),
        binary_settle_interval=float(os.getenv("BINARY_SETTLE_INTERVAL", "5")),
        toncenter_api_key=os.getenv("TONCENTER_API_KEY", "").strip(),
        toncenter_base=os.getenv("TONCENTER_BASE", "https://toncenter.com/api/v2").rstrip("/"),
        hot_wallet_mnemonic=os.getenv("HOT_WALLET_MNEMONIC", "").strip(),
        min_deposit_ton=float(os.getenv("MIN_DEPOSIT_TON", "1")),
        deposit_lookback=int(os.getenv("DEPOSIT_LOOKBACK", "50")),
        deposit_worker_enabled=_bool(os.getenv("DEPOSIT_WORKER_ENABLED"), True),
        deposit_worker_interval=float(os.getenv("DEPOSIT_WORKER_INTERVAL", "30")),
        withdraw_onchain_enabled=_bool(os.getenv("WITHDRAW_ONCHAIN_ENABLED"), True),
        auto_withdraw_enabled=_bool(os.getenv("AUTO_WITHDRAW_ENABLED"), True),
        auto_withdraw_max=float(os.getenv("AUTO_WITHDRAW_MAX", "20")),
        withdraw_min_ton=float(os.getenv("WITHDRAW_MIN_TON", "1")),
        withdraw_max_l0=float(os.getenv("WITHDRAW_MAX_L0", "0")),
        withdraw_max_l1=float(os.getenv("WITHDRAW_MAX_L1", "50")),
        withdraw_max_l2=float(os.getenv("WITHDRAW_MAX_L2", "500")),
        withdraw_daily_max_l0=float(os.getenv("WITHDRAW_DAILY_MAX_L0", "0")),
        withdraw_daily_max_l1=float(os.getenv("WITHDRAW_DAILY_MAX_L1", "100")),
        withdraw_daily_max_l2=float(os.getenv("WITHDRAW_DAILY_MAX_L2", "1000")),
        backup_enabled=_bool(os.getenv("BACKUP_ENABLED"), True),
        backup_interval_sec=float(os.getenv("BACKUP_INTERVAL_SEC", "3600")),
        backup_dir=os.getenv("BACKUP_DIR", "backups").strip(),
        app_version=os.getenv("APP_VERSION", "1.0.0").strip() or "1.0.0",
        app_changelog=os.getenv("APP_CHANGELOG", "").strip(),
        bot_username=os.getenv("BOT_USERNAME", "").strip().lstrip("@"),
        database_url=os.getenv("DATABASE_URL", "").strip(),
    )


settings = load_settings()
