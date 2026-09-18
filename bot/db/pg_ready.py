"""
Postgres readiness helpers.
Production still uses SQLite via get_db() until a controlled cutover.
When DATABASE_URL is set, health reports postgres as *planned* engine only.
"""
from __future__ import annotations

from bot.config import settings


def planned_engine() -> str:
    return "postgres" if (settings.database_url or "").strip() else "sqlite"


def migration_required() -> bool:
    return bool((settings.database_url or "").strip())
