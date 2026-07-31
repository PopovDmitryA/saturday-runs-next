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


def _guard(token: str, settings: Settings) -> str:
    secret = settings.sweep_hq_token
    if not secret or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=404, detail="Not found")
    dsn = os.getenv("PM_WORLD_DSN")
    if not dsn:
        raise HTTPException(status_code=503, detail="sweep DB not configured")
    return dsn


@router.get("/athletes")
def sweep_hq_athletes(
    token: Annotated[str, Query()] = "",
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> dict:
    """Последние 100 обработанных атлетов: id, статус, ФИО, пробежки/волонтёрство,
    самая частая локация + страна (флаг по iso2 на фронте)."""
    dsn = _guard(token, settings)
    import psycopg

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        rows = _rows(conn, """
            WITH recent AS (
                SELECT athlete_id, name, total_runs, status, parsed_at
                FROM athletes WHERE source='crawl'
                ORDER BY parsed_at DESC LIMIT 100
            ),
            loc AS (
                SELECT DISTINCT ON (athlete_id) athlete_id, event_slug, event_name
                FROM (
                    SELECT athlete_id, event_slug, event_name, count(*) AS cnt
                    FROM runs WHERE athlete_id IN (SELECT athlete_id FROM recent)
                    GROUP BY athlete_id, event_slug, event_name
                ) g
                ORDER BY athlete_id, cnt DESC
            )
            SELECT r.athlete_id, r.name, r.total_runs, r.status,
                   to_char(r.parsed_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS parsed_at,
                   COALESCE(vs.total_credits, 0) AS volunteer,
                   loc.event_name AS location, ec.country_name, ec.iso2
            FROM recent r
            LEFT JOIN loc ON loc.athlete_id = r.athlete_id
            LEFT JOIN volunteer_summary vs ON vs.athlete_id = r.athlete_id
            LEFT JOIN event_country ec
                   ON ec.slug = regexp_replace(loc.event_slug, '[^a-z0-9]', '', 'g')
            ORDER BY r.parsed_at DESC""")
    for r in rows:
        r["athlete_id"] = int(r["athlete_id"])
        r["total_runs"] = int(r["total_runs"] or 0)
        r["volunteer"] = int(r["volunteer"] or 0)
    return {"athletes": rows}


@router.get("/rate-history")
def sweep_hq_rate_history(
    token: Annotated[str, Query()] = "",
    hours: Annotated[int, Query(ge=1, le=24 * 14)] = 48,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> dict:
    """Сколько атлетов собрано за каждый час — динамика скорости обхода.
    fetched_at хранится на каждой строке crawl_queue постоянно, отдельного
    снапшота не нужно: считаем ретроспективно группировкой по часу."""
    dsn = _guard(token, settings)
    import psycopg

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        rows = _rows(conn, f"""
            SELECT to_char(date_trunc('hour', fetched_at), 'YYYY-MM-DD"T"HH24:00:00"Z"') AS hour,
                   count(*) AS collected
            FROM crawl_queue
            WHERE fetched_at > now() - interval '{hours} hours'
            GROUP BY 1 ORDER BY 1""")
    for r in rows:
        r["collected"] = int(r["collected"] or 0)
    return {"hours": rows}


@router.get("")
def sweep_hq(
    token: Annotated[str, Query()] = "",
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> dict:
    dsn = _guard(token, settings)

    import psycopg

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        prog = _rows(conn, """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status IN
                       ('collected','ok','not_found','registered_empty','unclassified')) AS done,
                   count(*) FILTER (WHERE status='collected') AS in_processing,
                   count(*) FILTER (WHERE fetched_at > now() - interval '24 hours') AS rate_24h,
                   count(*) FILTER (WHERE fetched_at > now() - interval '1 hour') AS rate_1h,
                   (SELECT count(*) FROM runs) AS runs,
                   (SELECT count(*) FROM athletes WHERE source='crawl'
                       AND parsed_at > now() - interval '24 hours') AS parse_rate_24h,
                   (SELECT count(*) FROM athletes WHERE source='crawl'
                       AND parsed_at > now() - interval '1 hour') AS parse_rate_1h
            FROM crawl_queue""")[0]
        vpn = _rows(conn, """
            SELECT name, account, collected_total, active_seconds, delay_sec,
                   captcha_total, captcha_solved,
                   to_char(last_captcha_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS last_captcha_at,
                   CASE WHEN account='mac' AND (worker_heartbeat_at IS NULL
                             OR worker_heartbeat_at <= now() - interval '90 seconds')
                             THEN 'off'   -- локальный скрипт: нет heartbeat = не запущен
                        WHEN NOT enabled THEN 'off'
                        WHEN cooldown_until > now() THEN 'cooldown'
                        WHEN worker_heartbeat_at > now() - interval '90 seconds' THEN 'working'
                        ELSE 'queued' END AS status,
                   GREATEST(0, EXTRACT(EPOCH FROM (cooldown_until - now())) / 3600) AS cooldown_hours,
                   to_char(last_ok_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS last_ok_at,
                   ban_level
            FROM sweep_exits WHERE account <> 'free'
            ORDER BY
                collected_total DESC,                   -- РЕЙТИНГ: сколько спарсил (главное)
                CASE WHEN account='mac' AND (worker_heartbeat_at IS NULL
                          OR worker_heartbeat_at <= now() - interval '90 seconds') THEN 3
                     WHEN NOT enabled THEN 3
                     WHEN cooldown_until > now() THEN 2
                     WHEN worker_heartbeat_at > now() - interval '90 seconds' THEN 0
                     ELSE 1 END,                        -- вторым: работает→очередь→отлёжка→выкл
                cooldown_until ASC NULLS FIRST, name""")
        free_sum = _rows(conn, """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE last_ok_at IS NOT NULL
                       AND (cooldown_until IS NULL OR cooldown_until<=now())) AS active,
                   count(*) FILTER (WHERE cooldown_until > now()) AS cooldown,
                   COALESCE(sum(collected_total), 0) AS collected
            FROM free_proxies""")[0]
        free_top = _rows(conn, """
            SELECT proxy, collected_total, ban_level, delay_sec, active_seconds,
                   CASE WHEN cooldown_until > now() THEN 'cooldown'
                        WHEN last_ok_at IS NOT NULL THEN 'working'
                        ELSE 'off' END AS status,
                   GREATEST(0, EXTRACT(EPOCH FROM (cooldown_until - now())) / 3600) AS cooldown_hours,
                   to_char(last_ok_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS last_ok_at
            FROM free_proxies
            ORDER BY collected_total DESC, last_ok_at DESC NULLS LAST LIMIT 300""")

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
        r["captcha_total"] = int(r.get("captcha_total") or 0)
        r["captcha_solved"] = int(r.get("captcha_solved") or 0)
        r["collected_total"] = int(r["collected_total"] or 0)
        r["active_seconds"] = int(r["active_seconds"] or 0)
        r["delay_sec"] = round(num(r["delay_sec"]), 1)
        r["cooldown_hours"] = round(num(r["cooldown_hours"]), 1)
        r["ban_level"] = int(r["ban_level"] or 0)
    for r in free_top:
        r["collected_total"] = int(r["collected_total"] or 0)
        r["active_seconds"] = int(r["active_seconds"] or 0)
        r["delay_sec"] = round(num(r["delay_sec"]), 1)
        r["cooldown_hours"] = round(num(r["cooldown_hours"]), 1)
        r["ban_level"] = int(r["ban_level"] or 0)

    return {
        "progress": {
            "done": done, "total": total, "remaining": remaining, "pct": pct,
            "collected": done,  # собрано = в папке (не распарсено) + распарсено
            "in_processing": int(prog["in_processing"] or 0),
            "runs": int(prog["runs"] or 0),
        },
        "rate_24h": rate_24h,
        "rate_1h": int(prog["rate_1h"] or 0),
        "parse_rate_24h": int(prog["parse_rate_24h"] or 0),
        "parse_rate_1h": int(prog["parse_rate_1h"] or 0),
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
