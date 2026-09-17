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
from bot.db.ledger import (
    BusinessRuleError,
    InsufficientBalance,
    InvalidAmount,
    TxKind,
    swap_ton_to_usdt, swap_usdt_to_ton,
    start_prop_challenge,
    get_prop_account,
    prop_virtual_trade,
    claim_prop_reward,
    list_prop_trades,
    hold_withdraw,
    complete_withdraw,
    reject_withdraw,
    credit_ton,
    debit_ton,
)
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


def _require_admin(request: web.Request):
    validated = _authenticate(request)
    if int(validated.user.id) != int(settings.admin_id):
        raise web.HTTPForbidden(text='{"error":"admin_only"}', content_type="application/json")
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
    """Quote for supported ledger pairs. Auth required."""
    validated = _authenticate(request)
    src = (request.query.get("from") or request.query.get("src") or "TON").upper()
    dst = (request.query.get("to") or request.query.get("dst") or "USDT").upper()
    fee_bps = 30
    pairs = {
        ("TON", "USDT"): ("TONUSDT", "ton", "usdt"),
        ("USDT", "TON"): ("TONUSDT", "usdt", "ton"),
    }
    if (src, dst) not in pairs:
        raise web.HTTPBadRequest(
            text='{"error":"unsupported_pair","detail":"Supported: TON↔USDT"}',
            content_type="application/json",
        )
    symbol, base, quote = pairs[(src, dst)]
    price = await get_market_price(symbol)
    if not price or price <= 0:
        raise web.HTTPServiceUnavailable(
            text='{"error":"price_unavailable"}', content_type="application/json"
        )
    # rate = how many dst per 1 src
    if src == "TON" and dst == "USDT":
        rate = float(price)
        min_in, max_in = 0.5, 500.0
    else:
        rate = 1.0 / float(price)
        min_in, max_in = 1.0, 2000.0
    # balances
    ton_b = usdt_b = 0.0
    try:
        import aiosqlite
        async with aiosqlite.connect(settings.db_name) as db:
            row = await (await db.execute(
                "SELECT balance, usdt_balance FROM users WHERE user_id=?",
                (validated.user.id,),
            )).fetchone()
            if row:
                ton_b, usdt_b = float(row[0] or 0), float(row[1] or 0)
    except Exception:
        pass
    return web.json_response({
        "ok": True,
        "from": src,
        "to": dst,
        "symbol": symbol,
        "rate": rate,
        "fee_bps": fee_bps,
        "min_in": min_in,
        "max_in": max_in,
        "balances": {"TON": ton_b, "USDT": usdt_b},
        "supported": ["TON/USDT", "USDT/TON"],
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


async def api_swap_execute(request: web.Request) -> web.Response:
    """Execute TON↔USDT ledger swap."""
    validated = _authenticate(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_json"}', content_type="application/json")
    src = str(body.get("from") or "TON").upper()
    dst = str(body.get("to") or "USDT").upper()
    try:
        amount = float(body.get("amount") or 0)
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_amount"}', content_type="application/json")

    fee_bps = 30
    rate_raw = await get_market_price("TONUSDT")
    if not rate_raw or rate_raw <= 0:
        raise web.HTTPServiceUnavailable(
            text='{"error":"price_unavailable"}', content_type="application/json"
        )

    try:
        if src == "TON" and dst == "USDT":
            if amount < 0.5:
                raise web.HTTPBadRequest(text='{"error":"min_amount"}', content_type="application/json")
            if amount > 500:
                raise web.HTTPBadRequest(text='{"error":"max_amount"}', content_type="application/json")
            fee = round(amount * fee_bps / 10000.0, 8)
            net = round(amount - fee, 8)
            out = round(net * float(rate_raw), 6)
            result = await swap_ton_to_usdt(
                validated.user.id, amount, out, rate=float(rate_raw), fee_ton=fee
            )
            return web.json_response({
                "ok": True,
                "from": "TON",
                "to": "USDT",
                "amount_in": amount,
                "amount_out": out,
                "fee": fee,
                "fee_asset": "TON",
                "rate": float(rate_raw),
                "balance_ton": result.ton_balance,
                "balance_usdt": result.usdt_balance,
            })
        if src == "USDT" and dst == "TON":
            if amount < 1.0:
                raise web.HTTPBadRequest(text='{"error":"min_amount"}', content_type="application/json")
            if amount > 2000:
                raise web.HTTPBadRequest(text='{"error":"max_amount"}', content_type="application/json")
            fee = round(amount * fee_bps / 10000.0, 8)
            net = round(amount - fee, 8)
            out = round(net / float(rate_raw), 8)
            result = await swap_usdt_to_ton(
                validated.user.id, amount, out, rate=float(rate_raw), fee_usdt=fee
            )
            return web.json_response({
                "ok": True,
                "from": "USDT",
                "to": "TON",
                "amount_in": amount,
                "amount_out": out,
                "fee": fee,
                "fee_asset": "USDT",
                "rate": 1.0 / float(rate_raw),
                "balance_ton": result.ton_balance,
                "balance_usdt": result.usdt_balance,
            })
        raise web.HTTPBadRequest(
            text='{"error":"unsupported_pair"}', content_type="application/json"
        )
    except web.HTTPException:
        raise
    except InsufficientBalance:
        raise web.HTTPPaymentRequired(
            text='{"error":"insufficient_balance"}', content_type="application/json"
        )
    except (InvalidAmount, BusinessRuleError) as exc:
        raise web.HTTPBadRequest(text='{"error":"%s"}' % exc, content_type="application/json")
    except Exception:
        logger.exception("swap execute failed")
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



async def api_prop_status(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    acc = await get_prop_account(validated.user.id)
    plans = [
        {
            "id": "10k",
            "plan_size": 10000.0,
            "fee": 50.0,
            "target_pct": 10,
            "max_dd_pct": 10,
            "profit_share": 0.8,
            "max_stake_pct": 10,
            "min_stake": 50,
        },
        {
            "id": "50k",
            "plan_size": 50000.0,
            "fee": 200.0,
            "target_pct": 10,
            "max_dd_pct": 10,
            "profit_share": 0.8,
            "max_stake_pct": 10,
            "min_stake": 50,
        },
    ]
    trades = await list_prop_trades(validated.user.id, limit=15)
    if not acc:
        return web.json_response({"ok": True, "account": None, "plans": plans, "trades": trades})
    plan = float(acc.get("plan_size") or 0)
    bal = float(acc.get("virtual_balance") or 0)
    share = float(acc.get("profit_share") or 0.8)
    status = acc.get("status") or "active"
    claimable = round(max(0.0, (bal - plan) * share), 8) if status == "passed" else 0.0
    return web.json_response({
        "ok": True,
        "account": {
            "plan_size": plan,
            "virtual_balance": bal,
            "status": status,
            "target": plan * 1.10,
            "dd_floor": plan * 0.90,
            "peak_balance": float(acc.get("peak_balance") or plan),
            "trades_count": int(acc.get("trades_count") or 0),
            "fee_paid": float(acc.get("fee_paid") or 0),
            "profit_share": share,
            "claimable": claimable,
            "progress_pct": round(((bal - plan) / plan) * 100, 2) if plan else 0,
        },
        "plans": plans,
        "trades": trades,
    })


async def api_prop_start(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_json"}', content_type="application/json")
    plan_id = str(body.get("plan") or body.get("plan_id") or "").lower()
    if plan_id in ("10k", "10000"):
        plan, fee = 10000.0, 50.0
    elif plan_id in ("50k", "50000"):
        plan, fee = 50000.0, 200.0
    else:
        raise web.HTTPBadRequest(text='{"error":"invalid_plan"}', content_type="application/json")
    try:
        result, size = await start_prop_challenge(
            validated.user.id, plan_size=plan, fee=fee, profit_share=0.8
        )
        return web.json_response({
            "ok": True,
            "plan_size": size,
            "fee": fee,
            "virtual_balance": size,
            "balance_ton": result.ton_balance,
            "status": "active",
            "target": size * 1.10,
            "dd_floor": size * 0.90,
            "profit_share": 0.8,
        })
    except InsufficientBalance:
        raise web.HTTPPaymentRequired(
            text='{"error":"insufficient_balance"}', content_type="application/json"
        )
    except (InvalidAmount, BusinessRuleError) as exc:
        raise web.HTTPBadRequest(text='{"error":"%s"}' % exc, content_type="application/json")


async def api_prop_trade(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_json"}', content_type="application/json")
    direction = str(body.get("direction") or "").lower()
    try:
        amount = float(body.get("amount") or 0)
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_amount"}', content_type="application/json")
    symbol = str(body.get("symbol") or "BTCUSDT").upper()

    entry = await get_market_price(symbol)
    if not entry or entry <= 0:
        raise web.HTTPServiceUnavailable(
            text='{"error":"price_unavailable"}', content_type="application/json"
        )
    await asyncio.sleep(2.5)
    exit_p = await get_market_price(symbol)
    if not exit_p or exit_p <= 0:
        exit_p = entry
    went_up = exit_p >= entry
    won = (direction == "up" and went_up) or (direction == "down" and not went_up)
    try:
        result = await prop_virtual_trade(
            validated.user.id,
            direction=direction,
            amount=amount,
            won=won,
            entry_price=entry,
            exit_price=exit_p,
            payout_rate=1.8,
        )
        return web.json_response({
            "ok": True,
            "won": won,
            "entry_price": entry,
            "exit_price": exit_p,
            "direction": direction,
            "amount": amount,
            **result,
        })
    except InsufficientBalance:
        raise web.HTTPPaymentRequired(
            text='{"error":"insufficient_virtual_balance"}', content_type="application/json"
        )
    except (InvalidAmount, BusinessRuleError) as exc:
        raise web.HTTPBadRequest(text='{"error":"%s"}' % exc, content_type="application/json")


async def api_prop_claim(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    try:
        result = await claim_prop_reward(validated.user.id)
        return web.json_response({"ok": True, **result})
    except BusinessRuleError as exc:
        raise web.HTTPBadRequest(text='{"error":"%s"}' % exc, content_type="application/json")
    except Exception:
        logger.exception("prop claim failed")
        raise web.HTTPInternalServerError(text='{"error":"server_error"}', content_type="application/json")


async def api_prop_history(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    try:
        limit = min(int(request.query.get("limit", "20")), 50)
    except ValueError:
        limit = 20
    rows = await list_prop_trades(validated.user.id, limit=limit)
    return web.json_response({"ok": True, "trades": rows})



async def api_withdraw(request: web.Request) -> web.Response:
    """Create withdraw request: holds TON immediately, optional auto on-chain under threshold."""
    validated = _authenticate(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_json"}', content_type="application/json")
    try:
        amount = float(body.get("amount") or 0)
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_amount"}', content_type="application/json")
    address = str(body.get("address") or "").strip()
    if amount < 1:
        raise web.HTTPBadRequest(text='{"error":"min_withdraw_1"}', content_type="application/json")
    if len(address) < 20:
        raise web.HTTPBadRequest(text='{"error":"invalid_address"}', content_type="application/json")
    try:
        result, req_id = await hold_withdraw(validated.user.id, amount, address=address)
    except InsufficientBalance:
        raise web.HTTPPaymentRequired(
            text='{"error":"insufficient_balance"}', content_type="application/json"
        )
    except (InvalidAmount, BusinessRuleError) as exc:
        raise web.HTTPBadRequest(text='{"error":"%s"}' % exc, content_type="application/json")

    auto_info = None
    try:
        from bot.services.withdraw import try_auto_withdraw
        auto_info = await try_auto_withdraw(req_id, amount, address)
    except Exception:
        logger.exception("auto withdraw hook failed")
        auto_info = {"ok": False, "reason": "auto_failed"}

    return web.json_response({
        "ok": True,
        "request_id": req_id,
        "amount": amount,
        "address": address,
        "balance_ton": result.ton_balance,
        "status": "pending",
        "auto": auto_info,
        "note": "Withdraw held. Processing may require admin if above auto threshold.",
    })



async def api_admin_stats(request: web.Request) -> web.Response:
    _require_admin(request)
    import aiosqlite
    from datetime import datetime
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        bal = await (await db.execute(
            "SELECT COALESCE(SUM(balance),0), COALESCE(SUM(usdt_balance),0) FROM users"
        )).fetchone()
        pwd = await (await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM requests WHERE req_type='withdraw' AND status='pending'"
        )).fetchone()
        open_bin = (await (await db.execute(
            "SELECT COUNT(*) FROM binary_trades WHERE status='open'"
        )).fetchone())[0]
        try:
            prop_a = (await (await db.execute(
                "SELECT COUNT(*) FROM prop_accounts WHERE status='active'"
            )).fetchone())[0]
        except Exception:
            prop_a = 0
        try:
            tickets = (await (await db.execute(
                "SELECT COUNT(*) FROM support_tickets WHERE status='open'"
            )).fetchone())[0]
        except Exception:
            tickets = 0
        today = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            new_u = (await (await db.execute(
                "SELECT COUNT(*) FROM users WHERE date(join_date)=date(?)", (today,)
            )).fetchone())[0]
        except Exception:
            new_u = 0
        try:
            tx_today = (await (await db.execute(
                "SELECT COUNT(*) FROM transactions WHERE date(created_at)=date(?)", (today,)
            )).fetchone())[0]
        except Exception:
            tx_today = 0
        kyc = await (await db.execute(
            "SELECT "
            "SUM(CASE WHEN kyc_level=0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN kyc_level=1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN kyc_level=2 THEN 1 ELSE 0 END) FROM users"
        )).fetchone()
    return web.json_response({
        "ok": True,
        "users": users,
        "ton": float(bal[0] or 0),
        "usdt": float(bal[1] or 0),
        "pending_withdraws": int(pwd[0] or 0),
        "pending_withdraw_ton": float(pwd[1] or 0),
        "open_binary": open_bin,
        "active_prop": prop_a,
        "open_tickets": tickets,
        "new_users_today": new_u,
        "tx_today": tx_today,
        "kyc": {"l0": int(kyc[0] or 0), "l1": int(kyc[1] or 0), "l2": int(kyc[2] or 0)},
        "freeze": bool(settings.emergency_freeze),
        "maintenance": bool(getattr(settings, "maintenance_mode", False)),
    })


async def api_admin_withdraws(request: web.Request) -> web.Response:
    _require_admin(request)
    import aiosqlite
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT request_id, user_id, amount, address, status, created_at
            FROM requests WHERE req_type='withdraw' AND status='pending'
            ORDER BY request_id ASC LIMIT 30
            """
        )).fetchall()
    return web.json_response({"ok": True, "items": [dict(r) for r in rows]})


async def api_admin_withdraw_action(request: web.Request) -> web.Response:
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_json"}', content_type="application/json")
    rid = int(body.get("request_id") or 0)
    action = str(body.get("action") or "").lower()
    if not rid:
        raise web.HTTPBadRequest(text='{"error":"bad_id"}', content_type="application/json")
    if action == "approve":
        await complete_withdraw(rid, tx_hash=f"admin_web_{rid}")
        return web.json_response({"ok": True, "status": "completed"})
    if action == "reject":
        res = await reject_withdraw(rid)
        return web.json_response({"ok": True, "status": "rejected", "balance_ton": res.ton_balance})
    raise web.HTTPBadRequest(text='{"error":"bad_action"}', content_type="application/json")


async def api_admin_user(request: web.Request) -> web.Response:
    _require_admin(request)
    import aiosqlite
    try:
        uid = int(request.query.get("user_id") or 0)
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"bad_user"}', content_type="application/json")
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT user_id, balance, usdt_balance, kyc_level, loan_amount, lang, phone, email, join_date FROM users WHERE user_id=?",
            (uid,),
        )).fetchone()
    if not row:
        raise web.HTTPNotFound(text='{"error":"not_found"}', content_type="application/json")
    return web.json_response({"ok": True, "user": dict(row)})


async def api_admin_credit(request: web.Request) -> web.Response:
    validated = _require_admin(request)
    try:
        body = await request.json()
        uid = int(body.get("user_id"))
        amount = float(body.get("amount"))
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_body"}', content_type="application/json")
    if amount <= 0:
        raise web.HTTPBadRequest(text='{"error":"amount"}', content_type="application/json")
    res = await credit_ton(uid, amount, kind=TxKind.ADMIN_CREDIT, meta={"by_admin": validated.user.id, "via": "web"})
    return web.json_response({"ok": True, "balance_ton": res.ton_balance})


async def api_admin_debit(request: web.Request) -> web.Response:
    validated = _require_admin(request)
    try:
        body = await request.json()
        uid = int(body.get("user_id"))
        amount = float(body.get("amount"))
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_body"}', content_type="application/json")
    if amount <= 0:
        raise web.HTTPBadRequest(text='{"error":"amount"}', content_type="application/json")
    try:
        res = await debit_ton(uid, amount, kind=TxKind.ADMIN_DEBIT, meta={"by_admin": validated.user.id, "via": "web"})
    except InsufficientBalance:
        raise web.HTTPPaymentRequired(text='{"error":"insufficient_balance"}', content_type="application/json")
    return web.json_response({"ok": True, "balance_ton": res.ton_balance})


async def api_admin_toggle(request: web.Request) -> web.Response:
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    key = str(body.get("key") or "")
    if key == "freeze":
        settings.emergency_freeze = not bool(settings.emergency_freeze)
        return web.json_response({"ok": True, "freeze": settings.emergency_freeze})
    if key == "maintenance":
        settings.maintenance_mode = not bool(getattr(settings, "maintenance_mode", False))
        return web.json_response({"ok": True, "maintenance": settings.maintenance_mode})
    raise web.HTTPBadRequest(text='{"error":"bad_key"}', content_type="application/json")


async def api_admin_kyc_list(request: web.Request) -> web.Response:
    _require_admin(request)
    import aiosqlite
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT user_id, phone, email, kyc_level, balance, join_date
            FROM users WHERE kyc_level IN (0,1)
            ORDER BY kyc_level DESC, user_id DESC LIMIT 40
            """
        )).fetchall()
    return web.json_response({"ok": True, "items": [dict(r) for r in rows]})


async def api_admin_kyc_set(request: web.Request) -> web.Response:
    _require_admin(request)
    import aiosqlite
    try:
        body = await request.json()
        uid = int(body.get("user_id"))
        level = int(body.get("level"))
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_body"}', content_type="application/json")
    if level not in (0, 1, 2):
        raise web.HTTPBadRequest(text='{"error":"bad_level"}', content_type="application/json")
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE users SET kyc_level=? WHERE user_id=?", (level, uid))
        try:
            if level >= 2:
                await db.execute(
                    "UPDATE kyc_submissions SET status='approved', reviewed_at=CURRENT_TIMESTAMP "
                    "WHERE user_id=? AND status='pending'",
                    (uid,),
                )
            elif level == 0:
                await db.execute(
                    "UPDATE kyc_submissions SET status='rejected', reviewed_at=CURRENT_TIMESTAMP "
                    "WHERE user_id=? AND status='pending'",
                    (uid,),
                )
        except Exception:
            pass
        await db.commit()
    paid = 0.0
    if level >= 2:
        try:
            from bot.db.users import pay_referral_l2_reward
            paid = await pay_referral_l2_reward(uid)
        except Exception:
            logger.exception("referral l2 reward failed")
    return web.json_response({"ok": True, "user_id": uid, "kyc_level": level, "referral_l2_paid": paid})


async def api_admin_tickets(request: web.Request) -> web.Response:
    _require_admin(request)
    import aiosqlite
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT ticket_id, user_id, message, status, created_at
            FROM support_tickets WHERE status='open'
            ORDER BY ticket_id DESC LIMIT 30
            """
        )).fetchall()
    return web.json_response({"ok": True, "items": [dict(r) for r in rows]})


async def api_admin_ticket_close(request: web.Request) -> web.Response:
    _require_admin(request)
    import aiosqlite
    try:
        body = await request.json()
        tid = int(body.get("ticket_id"))
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_body"}', content_type="application/json")
    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE support_tickets SET status='closed' WHERE ticket_id=?", (tid,))
        await db.commit()
    return web.json_response({"ok": True})


async def api_admin_recent_tx(request: web.Request) -> web.Response:
    _require_admin(request)
    import aiosqlite
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, user_id, kind, amount, currency, created_at
            FROM transactions ORDER BY id DESC LIMIT 25
            """
        )).fetchall()
    return web.json_response({"ok": True, "items": [dict(r) for r in rows]})


async def api_admin_kyc_submissions(request: web.Request) -> web.Response:
    _require_admin(request)
    import aiosqlite
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        try:
            rows = await (await db.execute(
                """
                SELECT id, user_id, email, status, created_at, passport_file_id, selfie_file_id
                FROM kyc_submissions WHERE status='pending'
                ORDER BY id DESC LIMIT 40
                """
            )).fetchall()
        except Exception:
            rows = []
    return web.json_response({"ok": True, "items": [dict(r) for r in rows]})


async def api_referral_me(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    from bot.db.users import ensure_referral_code
    code = await ensure_referral_code(validated.user.id)
    import aiosqlite
    count = 0
    referrer_id = None
    async with aiosqlite.connect(settings.db_name) as db:
        cur = await db.execute(
            "SELECT referral_count, referrer_id FROM users WHERE user_id=?",
            (validated.user.id,),
        )
        row = await cur.fetchone()
        if row:
            count = row[0] or 0
            referrer_id = row[1]
    bot_user = (getattr(settings, "bot_username", None) or "").lstrip("@")
    invite_link = f"https://t.me/{bot_user}?start={code}" if bot_user else ""
    return web.json_response({
        "ok": True,
        "code": code,
        "count": count,
        "referrer_id": referrer_id,
        "invite_link": invite_link,
        "bot_username": bot_user,
    })


async def api_referral_apply(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    try:
        body = await request.json()
        code = str(body.get("code") or "")
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_json"}', content_type="application/json")
    from bot.db.users import apply_referral_code
    ok, reason = await apply_referral_code(validated.user.id, code)
    if not ok:
        raise web.HTTPBadRequest(text='{"error":"%s"}' % reason, content_type="application/json")
    return web.json_response({"ok": True})


async def api_kyc_status(request: web.Request) -> web.Response:
    validated = _authenticate(request)
    uid = int(validated.user.id)
    import aiosqlite
    async with aiosqlite.connect(settings.db_name) as db:
        db.row_factory = aiosqlite.Row
        u = await (await db.execute(
            "SELECT kyc_level, phone, email, lang FROM users WHERE user_id=?", (uid,)
        )).fetchone()
        try:
            sub = await (await db.execute(
                """
                SELECT id, status, email, created_at FROM kyc_submissions
                WHERE user_id=? ORDER BY id DESC LIMIT 1
                """,
                (uid,),
            )).fetchone()
        except Exception:
            sub = None
    return web.json_response({
        "ok": True,
        "kyc_level": (u["kyc_level"] if u else 0) or 0,
        "phone": (u["phone"] if u else None),
        "email": (u["email"] if u else None),
        "lang": (u["lang"] if u and "lang" in u.keys() else "fa") or "fa",
        "submission": dict(sub) if sub else None,
        "referral_l1_reward": float(getattr(settings, "referral_l1_reward", 1.0) or 0),
    })


async def api_kyc_submit(request: web.Request) -> web.Response:
    """Accept email + base64 passport/selfie images from Mini App."""
    validated = _authenticate(request)
    uid = int(validated.user.id)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_json"}', content_type="application/json")
    email = str(body.get("email") or "").strip()
    passport_b64 = str(body.get("passport_b64") or "")
    selfie_b64 = str(body.get("selfie_b64") or "")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise web.HTTPBadRequest(text='{"error":"invalid_email"}', content_type="application/json")
    if not passport_b64 or not selfie_b64:
        raise web.HTTPBadRequest(text='{"error":"images_required"}', content_type="application/json")

    import aiosqlite
    import base64
    import re
    from pathlib import Path as FsPath

    def _decode(data: str) -> bytes:
        m = re.match(r"^data:image/[^;]+;base64,(.+)$", data, re.S)
        raw = m.group(1) if m else data
        return base64.b64decode(raw)

    try:
        passport_bytes = _decode(passport_b64)
        selfie_bytes = _decode(selfie_b64)
    except Exception:
        raise web.HTTPBadRequest(text='{"error":"invalid_image"}', content_type="application/json")
    if len(passport_bytes) > 5_000_000 or len(selfie_bytes) > 5_000_000:
        raise web.HTTPBadRequest(text='{"error":"image_too_large"}', content_type="application/json")

    root = FsPath(settings.db_name).resolve().parent / "kyc" / str(uid)
    root.mkdir(parents=True, exist_ok=True)
    pass_path = root / "passport.jpg"
    self_path = root / "selfie.jpg"
    pass_path.write_bytes(passport_bytes)
    self_path.write_bytes(selfie_bytes)

    async with aiosqlite.connect(settings.db_name) as db:
        await db.execute("UPDATE users SET email = ? WHERE user_id = ?", (email, uid))
        await db.execute(
            """
            INSERT INTO kyc_submissions (user_id, email, passport_file_id, selfie_file_id, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (uid, email, str(pass_path), str(self_path)),
        )
        await db.commit()

    # best-effort admin notify (text only; files on disk for ops)
    try:
        from aiogram import Bot
        bot = Bot(token=settings.bot_token)
        await bot.send_message(
            settings.admin_id,
            f"KYC Mini App submit\nUser: `{uid}`\nEmail: `{email}`\nFiles saved on server.\nApprove in Admin Console / bot.",
            parse_mode="Markdown",
        )
        await bot.session.close()
    except Exception:
        logger.exception("kyc admin notify failed")

    return web.json_response({"ok": True, "status": "pending"})

def create_api_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    
    app.router.add_get("/api/admin/stats", api_admin_stats)
    app.router.add_get("/api/admin/withdraws", api_admin_withdraws)
    app.router.add_post("/api/admin/withdraw-action", api_admin_withdraw_action)
    app.router.add_get("/api/admin/user", api_admin_user)
    app.router.add_post("/api/admin/credit", api_admin_credit)
    app.router.add_post("/api/admin/debit", api_admin_debit)
    app.router.add_post("/api/admin/toggle", api_admin_toggle)
    app.router.add_get("/api/admin/kyc", api_admin_kyc_list)
    app.router.add_get("/api/admin/kyc-submissions", api_admin_kyc_submissions)
    app.router.add_get("/api/referral/me", api_referral_me)
    app.router.add_get("/api/kyc/status", api_kyc_status)
    app.router.add_post("/api/kyc/submit", api_kyc_submit)
    app.router.add_post("/api/referral/apply", api_referral_apply)
    app.router.add_post("/api/admin/kyc-set", api_admin_kyc_set)
    app.router.add_get("/api/admin/tickets", api_admin_tickets)
    app.router.add_post("/api/admin/ticket-close", api_admin_ticket_close)
    app.router.add_get("/api/admin/transactions", api_admin_recent_tx)

    app.router.add_get("/api/health", health)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/transactions", api_transactions)
    app.router.add_post("/api/withdraw", api_withdraw)
    app.router.add_post("/api/ping", api_ping)
    app.router.add_get("/api/binary/config", api_binary_config)
    app.router.add_get("/api/binary/price", api_binary_price)
    app.router.add_get("/api/binary/klines", api_binary_klines)
    app.router.add_post("/api/binary/open", api_binary_open)
    app.router.add_get("/api/binary/history", api_binary_history)
    app.router.add_post("/api/binary/settle-due", api_binary_settle_due)
    app.router.add_get("/api/swap/quote", api_swap_quote)
    app.router.add_post("/api/swap/ton-usdt", api_swap_ton_usdt)
    app.router.add_post("/api/swap/execute", api_swap_execute)
    app.router.add_get("/api/prop/status", api_prop_status)
    app.router.add_post("/api/prop/start", api_prop_start)
    app.router.add_post("/api/prop/trade", api_prop_trade)
    app.router.add_post("/api/prop/claim", api_prop_claim)
    app.router.add_get("/api/prop/history", api_prop_history)
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
