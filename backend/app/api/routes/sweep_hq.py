"""Скрытое табло мирового обхода атлетов parkrun (страница /hq/<token>).

Публичный (без логина), но закрыт секретным токеном — при несовпадении 404,
чтобы не выдавать существование. Данные тянет из staging-БД обхода (pm-postgres,
DSN в env PM_WORLD_DSN), той же, что читает бот-команда /обход.
"""
from __future__ import annotations

import hmac
import os
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings, get_settings

router = APIRouter(prefix="/sweep-hq", tags=["sweep-hq"])

QUEUE_TOTAL_FALLBACK = 6_693_994  # для прогноза, если запрос не отдал total


def _rows(conn, sql: str) -> list[dict]:
    cur = conn.execute(sql)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


@router.get("")
def sweep_hq(
    token: Annotated[str, Query()] = "",
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> dict:
    secret = settings.sweep_hq_token
    if not secret or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=404, detail="Not found")

    dsn = os.getenv("PM_WORLD_DSN")
    if not dsn:
        raise HTTPException(status_code=503, detail="sweep DB not configured")

    import psycopg

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        prog = _rows(conn, """
            SELECT count(*) FILTER (WHERE status<>'pending') AS done,
                   count(*) AS total,
                   count(*) FILTER (WHERE fetched_at > now() - interval '24 hours') AS rate_24h,
                   (SELECT count(*) FROM athletes WHERE source='crawl') AS collected,
                   (SELECT count(*) FROM runs) AS runs
            FROM crawl_queue""")[0]
        vpn = _rows(conn, """
            SELECT name, account, collected_total, active_seconds,
                   CASE WHEN NOT enabled THEN 'off'
                        WHEN cooldown_until > now() THEN 'cooldown'
                        ELSE 'working' END AS status,
                   GREATEST(0, EXTRACT(EPOCH FROM (cooldown_until - now())) / 3600) AS cooldown_hours,
                   to_char(last_ok_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS last_ok_at,
                   ban_level
            FROM sweep_exits WHERE account <> 'free'
            ORDER BY (CASE WHEN enabled AND (cooldown_until IS NULL OR cooldown_until<=now()) THEN 0 ELSE 1 END),
                     collected_total DESC, name""")
        free_sum = _rows(conn, """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE last_ok_at IS NOT NULL
                       AND (cooldown_until IS NULL OR cooldown_until<=now())) AS active,
                   count(*) FILTER (WHERE cooldown_until > now()) AS cooldown,
                   COALESCE(sum(collected_total), 0) AS collected
            FROM free_proxies""")[0]
        free_top = _rows(conn, """
            SELECT proxy, collected_total, ban_level,
                   CASE WHEN cooldown_until > now() THEN 'cooldown'
                        WHEN last_ok_at IS NOT NULL THEN 'working'
                        ELSE 'off' END AS status,
                   GREATEST(0, EXTRACT(EPOCH FROM (cooldown_until - now())) / 3600) AS cooldown_hours,
                   to_char(last_ok_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS last_ok_at
            FROM free_proxies
            ORDER BY collected_total DESC, last_ok_at DESC NULLS LAST LIMIT 50""")

    done = int(prog["done"] or 0)
    total = int(prog["total"] or 0) or QUEUE_TOTAL_FALLBACK
    remaining = max(0, total - done)
    rate_24h = int(prog["rate_24h"] or 0)
    pct = round(done / total * 100, 3) if total else 0.0

    forecast: dict = {"days": None, "date": None}
    if rate_24h > 0:
        days = remaining / rate_24h
        forecast = {"days": round(days, 1),
                    "date": (date.today() + timedelta(days=days)).isoformat()}

    def num(x) -> float:
        return float(x) if x is not None else 0.0

    for r in vpn:
        r["collected_total"] = int(r["collected_total"] or 0)
        r["active_seconds"] = int(r["active_seconds"] or 0)
        r["cooldown_hours"] = round(num(r["cooldown_hours"]), 1)
        r["ban_level"] = int(r["ban_level"] or 0)
    for r in free_top:
        r["collected_total"] = int(r["collected_total"] or 0)
        r["cooldown_hours"] = round(num(r["cooldown_hours"]), 1)
        r["ban_level"] = int(r["ban_level"] or 0)

    return {
        "progress": {
            "done": done, "total": total, "remaining": remaining, "pct": pct,
            "collected": int(prog["collected"] or 0), "runs": int(prog["runs"] or 0),
        },
        "rate_24h": rate_24h,
        "forecast": forecast,
        "vpn": vpn,
        "free": {
            "summary": {
                "total": int(free_sum["total"] or 0),
                "active": int(free_sum["active"] or 0),
                "cooldown": int(free_sum["cooldown"] or 0),
                "collected": int(free_sum["collected"] or 0),
            },
            "top": free_top,
        },
    }
