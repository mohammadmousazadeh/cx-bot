#!/usr/bin/env python3
"""Offline security/regression checks."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

errors: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print("PASS", name)
    else:
        errors.append("%s: %s" % (name, detail))
        print("FAIL", name, detail)


def main() -> int:
    for f in (ROOT / "bot").rglob("*.py"):
        try:
            ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errors.append("syntax %s: %s" % (f, e))
    check("syntax_all_bot_modules", not any(e.startswith("syntax") for e in errors))

    from bot.security import sanitize_amount, daily_bonus_amount, rate_limit, map_predict_symbol

    try:
        sanitize_amount(-1)
        check("sanitize_rejects_negative", False)
    except ValueError:
        check("sanitize_rejects_negative", True)

    try:
        sanitize_amount(float("nan"))
        check("sanitize_rejects_nan", False)
    except ValueError:
        check("sanitize_rejects_nan", True)

    a = daily_bonus_amount(1, "2026-01-01")
    b = daily_bonus_amount(1, "2026-01-01")
    check("daily_bonus_deterministic", a == b and 0.2 <= a <= 1.0, str(a))
    check("predict_symbol_map", map_predict_symbol("BTC") == "BTCUSDT")

    rate_limit("t:x", limit=2, window_sec=60)
    rate_limit("t:x", limit=2, window_sec=60)
    check("rate_limit_blocks", not rate_limit("t:x", limit=2, window_sec=60))

    from bot.services.telegram_auth import validate_init_data, InitDataError

    try:
        validate_init_data("user=%7B%7D&auth_date=1&hash=00", "fake:token", max_age_seconds=10**9)
        check("initdata_rejects_bad_hash", False)
    except InitDataError:
        check("initdata_rejects_bad_hash", True)

    if errors:
        print("\nFAILED:")
        for e in errors:
            print("-", e)
        return 1
    print("\nAll security self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
