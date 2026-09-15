"""
Lightweight aiohttp API for Mini Apps.

Endpoints:
  GET  /api/health
  GET  /api/me          — authenticated user profile + balances
  GET  /api/transactions — recent ledger rows
  POST /api/ping        — validate initData only

Auth: header  X-Telegram-Init-Data: <Telegram.WebApp.initData>
  or  Authorization: tma <initData>
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiohttp import web

from bot.config import settings
from bot.db import get_user_data, init_db
from bot.db.ledger import list_transactions
from bot.services.binary import (
    get_config as binary_get_config,
    open_binary_trade,
    list_user_trades,
    settle_due_trades,
)
from bot.services.market import get_market_price, get_klines
from bot.services.sniper import (
    get_config as sniper_get_config,
    open_sniper_round,
    settle_sniper_round,
    list_sniper_rounds,
)
from bot.db.ledger import BusinessRuleError, InsufficientBalance, InvalidAmount, swap_ton_to_usdt
from bot.security import rate_limit, require_not_frozen, sanitize_amount
from bot.services.telegram_auth import (
    InitDataError,
    extract_init_data_from_headers,
    validate_init_data,
)

logger = logging.getLogger("cx.api")


def _cors_headers(request: web.Request) -> dict:
    origin = request.headers.get("Origin", "*")
    # In production set WEBAPP_CORS_ORIGINS to your Pages domain(s)
    allowed = settings.webapp_cors_origins
    if allowed and allowed != ["*"]:
        if origin not in allowed:
            origin = allowed[0]
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data, Authorization, X-CX-Auth",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Max-Age": "86400",
    }


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_cors_headers(request))
    try:
        resp = await handler(request)
    except web.HTTPException as exc:
        exc.headers.update(_cors_headers(request))
        raise
    if isinstance(resp, web.StreamResponse):
        resp.headers.update(_cors_headers(request))
    return resp


def _authenticate(request: web.Request):
    raw = extract_init_data_from_headers(dict(request.headers))
    if raw:
        try:
            validated = validate_init_data(
                raw,
                settings.bot_token,
                max_age_seconds=settings.webapp_init_max_age,
            )
        except InitDataError as exc:
            logger.warning("initData rejected: %s", exc)
            raise web.HTTPUnauthorized(
                text='{"error":"invalid_init_data","detail":"%s"}' % exc,
                content_type="application/json",
            ) from exc
        uid = int(validated.user.id)
    else:
        # Fallback: signed token from bot keyboard URL (Iran/VPN initData issues)
        from bot.services.webapp_token import verify_webapp_token
        hdr = request.headers.get("X-CX-Auth") or ""
        uid_s = exp_s = sig = ""
        if hdr.startswith("v1:"):
            parts = hdr.split(":")
            if len(parts) >= 4:
                uid_s, exp_s, sig = parts[1], parts[2], parts[3]
        if not uid_s:
            uid_s = request.rel_url.query.get("uid", "")
            exp_s = request.rel_url.query.get("exp", "")
            sig = request.rel_url.query.get("sig", "")
        try:
            validated = verify_webapp_token(int(uid_s), int(exp_s), sig, settings.bot_token)
            uid = int(validated.user.id)
        except Exception as exc:
            raise web.HTTPUnauthorized(
                text='{"error":"missing_init_data","detail":"%s"}' % exc,
                content_type="application/json",
            ) from exc
    if not rate_limit("api:%s" % uid, limit=60, window_sec=60):
        raise web.HTTPTooManyRequests(
            text='{"error":"rate_limited"}',
            content_type="application/json",
        )
    try:
        require_not_frozen(uid)
    except PermissionError as exc:
        raise web.HTTPServiceUnavailable(
            text='{"error":"%s"}' % exc,
            content_type="application/json",
        )
    return validated


async def health(_request: web.Request) -> web.Response:
    from bot.config import settings as _s
    return web.json_response({
        "ok": True,
        "service": "cx-api",
        "auto_withdraw_max": getattr(_s, "auto_withdraw_max", None),
        "backup_enabled": getattr(_s, "backup_enabled", False),
        "database_url_set": bool(getattr(_s, "database_url", "")),
    })



async def api_me(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    uid = validated.user.id
    row = await get_user_data(uid)
    # row: lang, kyc, is_vip, loan, balance, phone, pin, cooldown, whitelist, usdt, email
    payload = {
        "ok": True,
        "user": {
            "id": validated.user.id,
            "username": validated.user.username,
            "first_name": validated.user.first_name,
            "language_code": validated.user.language_code,
        },
        "account": {
            "lang": row[0],
            "kyc_level": row[1],
            "is_vip": bool(row[2]),
            "loan_amount": row[3],
            "balance_ton": row[4],
            "usdt_balance": row[9],
            "has_pin": bool(row[6]),
            "whitelist_address": row[8],
            "email": row[10],
        },
        "deposit": {
            "wallet": settings.payment_wallet or settings.exchange_wallet or "",
            "memo": "cx_%s" % uid,
            "min_ton": float(getattr(settings, "min_deposit_ton", 1.0) or 1.0),
            "network": "TON",
        },
        "auth_date": validated.auth_date,
    }
    return web.json_response(payload)


async def api_transactions(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    try:
        limit = min(int(request.query.get("limit", "20")), 50)
    except ValueError:
        limit = 20
    rows = await list_transactions(validated.user.id, limit=limit)
    return web.json_response({"ok": True, "transactions": rows})


async def api_ping(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    return web.json_response(
        {
            "ok": True,
            "user_id": validated.user.id,
            "auth_date": validated.auth_date,
        }
    )




async def api_binary_config(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "config": binary_get_config()})


async def api_binary_price(request: web.Request) -> web.Response:
    symbol = (request.query.get("symbol") or "BTCUSDT").upper()
    price = await get_market_price(symbol)
    return web.json_response({
        "ok": True,
        "symbol": symbol,
        "price": price,
    })


async def api_binary_open(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(
            text='{"error":"invalid_json"}', content_type="application/json"
        )
    try:
        result = await open_binary_trade(
            validated.user.id,
            direction=str(body.get("direction", "")),
            amount=float(body.get("amount", 0)),
            duration_sec=int(body.get("duration_sec", 60)),
            symbol=str(body.get("symbol", "BTCUSDT")),
            client_request_id=(str(body["client_request_id"]) if body.get("client_request_id") else None),
        )
        return web.json_response(result)
    except InsufficientBalance:
        raise web.HTTPPaymentRequired(
            text='{"error":"insufficient_balance"}', content_type="application/json"
        )
    except (InvalidAmount, BusinessRuleError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=f'{{"error":"{exc}"}}', content_type="application/json"
        )
    except Exception as exc:
        logger.exception("binary open failed")
        raise web.HTTPInternalServerError(
            text=f'{{"error":"server_error","detail":"{exc}"}}',
            content_type="application/json",
        )


async def api_binary_history(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    only_open = request.query.get("open", "").lower() in ("1", "true", "yes")
    try:
        limit = min(int(request.query.get("limit", "20")), 50)
    except ValueError:
        limit = 20
    rows = await list_user_trades(
        validated.user.id, limit=limit, only_open=only_open
    )
    return web.json_response({"ok": True, "trades": rows})


async def api_binary_settle_due(request: web.Request) -> web.Response:
    """Internal/MVP endpoint: settle expired trades. Protect in production."""
    validated = _authenticate(request)
    # For MVP any authenticated user can trigger global settle of due trades
    # (settlement is server-side price based). Later restrict to admin/cron.
    results = await settle_due_trades(limit=50)
    return web.json_response({"ok": True, "settled": results, "by": validated.user.id})



async def api_binary_klines(request: web.Request) -> web.Response:
    symbol = (request.query.get("symbol") or "BTCUSDT").upper()
    interval = request.query.get("interval") or "1m"
    try:
        limit = min(int(request.query.get("limit", "100")), 500)
    except ValueError:
        limit = 100
    candles = await get_klines(symbol, interval=interval, limit=limit)
    return web.json_response({"ok": True, "symbol": symbol, "interval": interval, "candles": candles})



async def api_swap_quote(request: web.Request) -> web.Response:
    """Public-ish quote; still requires auth to avoid abuse."""
    _authenticate(request)
    symbol = (request.query.get("symbol") or "TONUSDT").upper()
    price = await get_market_price(symbol)
    if not price:
        raise web.HTTPServiceUnavailable(
            text='{"error":"price_unavailable"}', content_type="application/json"
        )
    fee_bps = 30  # 0.30%
    return web.json_response({
        "ok": True,
        "symbol": symbol,
        "rate": price,
        "fee_bps": fee_bps,
        "min_ton": 0.5,
        "max_ton": 500.0,
    })


async def api_swap_ton_usdt(request: web.Request) -> web.Response:
    """Execute TON -> USDT internal ledger swap at live rate minus fee."""
    validated = _authenticate(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_json"}', content_type="application/json")
    try:
        ton_amount = float(body.get("amount", 0))
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_amount"}', content_type="application/json")
    if ton_amount < 0.5:
        raise web.HTTPBadRequest(text='{"error":"min_amount_0.5"}', content_type="application/json")
    if ton_amount > 500:
        raise web.HTTPBadRequest(text='{"error":"max_amount_500"}', content_type="application/json")

    rate = await get_market_price("TONUSDT")
    if not rate or rate <= 0:
        raise web.HTTPServiceUnavailable(
            text='{"error":"price_unavailable"}', content_type="application/json"
        )
    fee_bps = 30
    fee_ton = round(ton_amount * fee_bps / 10000.0, 8)
    net_ton = round(ton_amount - fee_ton, 8)
    if net_ton <= 0:
        raise web.HTTPBadRequest(text='{"error":"amount_too_small"}', content_type="application/json")
    usdt_amount = round(net_ton * rate, 6)
    try:
        result = await swap_ton_to_usdt(
            validated.user.id,
            ton_amount,
            usdt_amount,
            rate=rate,
            fee_ton=fee_ton,
        )
        return web.json_response({
            "ok": True,
            "ton_spent": ton_amount,
            "fee_ton": fee_ton,
            "usdt_received": usdt_amount,
            "rate": rate,
            "balance_ton": result.ton_balance,
            "balance_usdt": result.usdt_balance,
        })
    except InsufficientBalance:
        raise web.HTTPPaymentRequired(
            text='{"error":"insufficient_balance"}', content_type="application/json"
        )
    except (InvalidAmount, BusinessRuleError) as exc:
        raise web.HTTPBadRequest(text='{"error":"%s"}' % exc, content_type="application/json")
    except Exception:
        logger.exception("swap failed")
        raise web.HTTPInternalServerError(text='{"error":"server_error"}', content_type="application/json")

async def api_sniper_config(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "config": sniper_get_config()})


async def api_sniper_open(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_json"}', content_type="application/json")
    try:
        result = await open_sniper_round(
            validated.user.id,
            direction=str(body.get("direction", "")),
            amount=float(body.get("amount", 0)),
            symbol=str(body.get("symbol", "BTCUSDT")),
        )
        return web.json_response(result)
    except InsufficientBalance:
        raise web.HTTPPaymentRequired(text='{"error":"insufficient_balance"}', content_type="application/json")
    except (InvalidAmount, BusinessRuleError, ValueError) as exc:
        raise web.HTTPBadRequest(text='{"error":"%s"}' % exc, content_type="application/json")
    except Exception as exc:
        logger.exception("sniper open failed")
        raise web.HTTPInternalServerError(text='{"error":"server_error"}', content_type="application/json")


async def api_sniper_settle(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_json"}', content_type="application/json")
    try:
        rid = int(body.get("round_id"))
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"round_id_required"}', content_type="application/json")
    # ownership check
    rows = await list_sniper_rounds(validated.user.id, limit=50)
    if not any(int(r["id"]) == rid for r in rows):
        raise web.HTTPForbidden(text='{"error":"not_your_round"}', content_type="application/json")
    exit_price = body.get("exit_price")
    try:
        exit_price_f = float(exit_price) if exit_price is not None else None
    except Exception:
        exit_price_f = None
    try:
        result = await settle_sniper_round(rid, exit_price=exit_price_f)
        return web.json_response(result)
    except BusinessRuleError as exc:
        raise web.HTTPBadRequest(text='{"error":"%s"}' % exc, content_type="application/json")


async def api_sniper_history(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    try:
        limit = min(int(request.query.get("limit", "20")), 50)
    except ValueError:
        limit = 20
    rows = await list_sniper_rounds(validated.user.id, limit=limit)
    return web.json_response({"ok": True, "rounds": rows})

def create_api_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/transactions", api_transactions)
    app.router.add_post("/api/ping", api_ping)
    app.router.add_get("/api/binary/config", api_binary_config)
    app.router.add_get("/api/binary/price", api_binary_price)
    app.router.add_get("/api/binary/klines", api_binary_klines)
    app.router.add_post("/api/binary/open", api_binary_open)
    app.router.add_get("/api/binary/history", api_binary_history)
    app.router.add_post("/api/binary/settle-due", api_binary_settle_due)
    app.router.add_get("/api/swap/quote", api_swap_quote)
    app.router.add_post("/api/swap/ton-usdt", api_swap_ton_usdt)
    app.router.add_get("/api/sniper/config", api_sniper_config)
    app.router.add_post("/api/sniper/open", api_sniper_open)
    app.router.add_post("/api/sniper/settle", api_sniper_settle)
    app.router.add_get("/api/sniper/history", api_sniper_history)
    app.router.add_route("OPTIONS", "/api/{tail:.*}", health)
    return app


async def start_api_server(host: str | None = None, port: int | None = None) -> web.AppRunner:
    await init_db()
    host = host or settings.api_host
    port = port or settings.api_port
    app = create_api_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("WebApp API listening on http://%s:%s", host, port)
    return runner
