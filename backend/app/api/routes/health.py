from __future__ import annotations

import redis
from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def readiness() -> dict[str, object]:
    settings = get_settings()
    db_ok = False
    redis_ok = False

    try:
        from sqlalchemy import text

        from app.db.session import get_engine

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        client = redis.from_url(settings.redis_url)
        redis_ok = client.ping() is True
    except Exception:
        redis_ok = False

    status = "ok" if db_ok and redis_ok else "degraded"
    return {"status": status, "database": db_ok, "redis": redis_ok}
