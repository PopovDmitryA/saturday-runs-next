"""Скрытое табло мирового обхода атлетов parkrun (страница /hq/<token>).

Публичный (без логина), но закрыт секретным токеном — при несовпадении 404,
чтобы не выдавать существование. Данные тянет из staging-БД обхода (pm-postgres,
DSN в env PM_WORLD_DSN).

Считать разделы роут больше не пытается: их раз в три минуты готовит задача
sweep_hq_snapshot и складывает в таблицу hq_snapshot, здесь остаётся поиск по
первичному ключу. Запросы и сборка ответа живут в sweep_hq_snapshot_service —
там же, откуда их берёт задача, чтобы снимок и запасной расчёт не разъехались.
"""
from __future__ import annotations

import hmac
import os
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings, get_settings
from app.services import sweep_hq_snapshot_service as snapshot

router = APIRouter(prefix="/sweep-hq", tags=["sweep-hq"])

# Снимок обновляется раз в 3 минуты, так что короткий кэш в памяти лишь
# срезает повторные коннекты к staging-БД при обновлении страницы.
CACHE_TTL_SECONDS = 30
_cache: dict[str, tuple[float, dict]] = {}


def _dsn_or_503() -> str:
    dsn = os.getenv("PM_WORLD_DSN")
    if not dsn:
        raise HTTPException(status_code=503, detail="sweep DB not configured")
    return dsn


def _guard(token: str, settings: Settings) -> str:
    secret = settings.sweep_hq_token
    if not secret or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=404, detail="Not found")
    return _dsn_or_503()


def _section(key: str) -> dict:
    """Готовый раздел из снимка.

    Если снимка ещё нет — считаем на месте: страница должна работать сразу
    после выката, не дожидаясь первого прогона расписания. В ответе
    `snapshot_at` — время расчёта (None, когда посчитали прямо сейчас).
    """
    now = time.time()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    dsn = _dsn_or_503()
    import psycopg

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        stored = snapshot.read_section(conn, key)
        if stored is not None:
            payload, computed_at = stored
            data = dict(payload, snapshot_at=computed_at)
        else:
            data = dict(snapshot.SECTIONS[key](conn), snapshot_at=None)
    _cache[key] = (now, data)
    return data


@router.get("/public")
def sweep_public() -> dict:
    """ПУБЛИЧНОЕ табло прогресса обхода (страница /world), без токена.

    Сознательно отдаёт только обезличенные агрегаты — состав полей и причины
    описаны у compute_public в sweep_hq_snapshot_service.
    """
    return _section("public")


@router.get("/public/rate-history")
def sweep_public_rate_history(
    hours: Annotated[int, Query(ge=0, le=24 * 365)] = 48,
) -> dict:
    """Публичная динамика темпа сбора по часам. hours=0 — весь период.
    Без имён/прокси — просто «сколько ID проверено за час»."""
    full = _section("rate_history")
    data = snapshot.slice_hours(full, hours)
    data["snapshot_at"] = full.get("snapshot_at")
    return data


@router.get("/rate-history")
def sweep_hq_rate_history(
    token: Annotated[str, Query()] = "",
    hours: Annotated[int, Query(ge=0, le=24 * 365)] = 48,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> dict:
    """Сколько атлетов собрано за каждый час — динамика скорости обхода.
    hours=0 — весь период (без фильтра по времени)."""
    _guard(token, settings)
    full = _section("rate_history")
    data = snapshot.slice_hours(full, hours)
    data["snapshot_at"] = full.get("snapshot_at")
    return data


@router.get("")
def sweep_hq(
    token: Annotated[str, Query()] = "",
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> dict:
    _guard(token, settings)
    return _section("hq")
