"""Ежедневный сбор погоды на стартах из Open-Meteo с отчётом в Telegram.

Бесплатный тариф Open-Meteo — 10 000 условных вызовов в сутки, сброс в полночь
UTC; год одной локации стоит ~26. Задача идёт в 03:20 по Москве, собирает
недостающие даты по всему периметру, пока не упрётся в лимит, и присылает
сводку админу. Когда бэкфил закончится, тот же прогон каждую неделю докачивает
свежие субботы (одна дата на локацию — копейки по весу).

Часовой лимит здесь не пережидаем (воркер общий): считаем его концом прогона.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.db.session import get_session_factory
from app.services.admin_telegram_notify import send_admin_report
from app.services.weather_service import ScopeRunSummary, collect_scope, format_run_report
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

REPORT_TIMEZONE = ZoneInfo("Europe/Moscow")


@celery_app.task(name="weather.collect_start_weather", time_limit=3 * 3600, soft_time_limit=3 * 3600 - 60)
def collect_start_weather_task() -> dict[str, object]:
    db = get_session_factory()()
    summary = ScopeRunSummary()
    try:
        with httpx.Client(headers={"User-Agent": "run5k.run weather collector"}) as client:
            summary = collect_scope(db, client, wait_hourly_reset=False)
    except Exception as exc:  # noqa: BLE001 — отчёт важнее трейсбека в логе воркера
        # Сюда попадает только то, что не поймал collect_scope (БД, сеть до первой локации).
        logger.exception("Сбор погоды на стартах упал")
        db.rollback()
        summary.error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        db.close()

    # Тишина, когда докачивать нечего: после бэкфила это будни между субботами.
    if summary.rows_written or summary.stopped_by_limit or summary.error or summary.api_calls:
        send_admin_report(format_run_report(summary, when=datetime.now(REPORT_TIMEZONE)))

    return {
        "rows_written": summary.rows_written,
        "api_calls": summary.api_calls,
        "locations_complete": summary.locations_complete,
        "scope_locations": summary.scope_locations,
        "stopped_by_limit": summary.stopped_by_limit,
        "finished": summary.finished,
        "error": summary.error,
    }
