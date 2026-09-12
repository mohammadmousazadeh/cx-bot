"""
CX Bot entrypoint — production ready.

Modes:
  - Long-polling (default): no WEBHOOK_URL
  - Webhook (production): set WEBHOOK_URL to public HTTPS base
                          e.g. https://api.example.com

Both modes serve the Mini App API on the same HTTP server when enabled.

    python -m bot.main
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from bot.storage import create_fsm_storage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from bot.config import settings
from bot.db import init_db
from bot.handlers import router
from bot.middlewares import AccessGuardMiddleware
from bot.workers import (
    start_binary_settler,
    stop_binary_settler,
    start_deposit_scanner,
    stop_deposit_scanner,
    start_backup_worker,
    stop_backup_worker,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("cx.bot")


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=create_fsm_storage())
    dp.update.middleware(AccessGuardMiddleware())
    dp.include_router(router)
    return dp


def build_bot() -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )


def use_webhook() -> bool:
    return bool(settings.webhook_url)


def full_webhook_url() -> str:
    """Public HTTPS URL Telegram will POST updates to."""
    base = settings.webhook_url.rstrip("/")
    path = settings.webhook_path
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


async def on_startup_common(bot: Bot) -> None:
    await init_db()
    me = await bot.get_me()
    logger.info("Bot online as @%s (id=%s)", me.username, me.id)
    logger.info(
        "mode=%s db=%s api=%s:%s webapp=%s",
        "webhook" if use_webhook() else "polling",
        settings.db_name,
        settings.api_host,
        settings.api_port,
        settings.webapp_base_url,
    )


async def run_polling() -> None:
    bot = build_bot()
    dp = build_dispatcher()

    async def _startup() -> None:
        await on_startup_common(bot)
        await bot.delete_webhook(drop_pending_updates=True)

    dp.startup.register(_startup)

    api_runner = None
    if settings.api_enabled:
        from bot.api import start_api_server

        api_runner = await start_api_server()

    if settings.binary_settler_enabled:
        start_binary_settler(bot)
    if settings.deposit_worker_enabled:
        start_deposit_scanner(bot)
    if settings.backup_enabled:
        start_backup_worker()

    logger.info("Starting long-polling…")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await stop_binary_settler()
        await stop_deposit_scanner()
        await stop_backup_worker()
        if api_runner is not None:
            await api_runner.cleanup()
        await bot.session.close()


async def run_webhook() -> None:
    """
    Single aiohttp app:
      - POST {webhook_path}  → Telegram updates
      - /api/*               → Mini App authenticated API
    """
    from bot.api.app import create_api_app

    bot = build_bot()
    dp = build_dispatcher()

    webhook_endpoint = full_webhook_url()
    logger.info("Registering Telegram webhook → %s", webhook_endpoint)

    async def _on_startup() -> None:
        await on_startup_common(bot)
        await bot.set_webhook(
            url=webhook_endpoint,
            secret_token=settings.webhook_secret or None,
            drop_pending_updates=settings.webhook_drop_pending,
            allowed_updates=dp.resolve_used_update_types(),
        )
        info = await bot.get_webhook_info()
        logger.info(
            "Webhook set: url=%s pending=%s",
            info.url,
            info.pending_update_count,
        )

    async def _on_shutdown() -> None:
        logger.info("Removing webhook…")
        try:
            await bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            logger.exception("delete_webhook failed")
        await bot.session.close()

    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    # Base app = Mini App API routes + CORS
    if settings.api_enabled:
        app = create_api_app()
    else:
        app = web.Application()

    # Mount aiogram webhook handler
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret or None,
    ).register(app, path=settings.webhook_path)

    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.api_host, settings.api_port)
    await site.start()
    logger.info(
        "HTTP server listening on http://%s:%s (webhook path %s)",
        settings.api_host,
        settings.api_port,
        settings.webhook_path,
    )

    if settings.binary_settler_enabled:
        start_binary_settler(bot)
    if settings.deposit_worker_enabled:
        start_deposit_scanner(bot)
    if settings.backup_enabled:
        start_backup_worker()

    # Run forever
    stop = asyncio.Event()
    try:
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await stop_binary_settler()
        await stop_deposit_scanner()
        await stop_backup_worker()
        await runner.cleanup()


async def run() -> None:
    if use_webhook():
        await run_webhook()
    else:
        await run_polling()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Stopped by user")


if __name__ == "__main__":
    main()
