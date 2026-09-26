"""Ежедневный сбор погоды на стартах из Open-Meteo с отчётом в Telegram.

Бесплатный тариф Open-Meteo — 10 000 условных вызовов в сутки, сброс в полночь
UTC; год одной локации стоит ~26. Задача идёт в 03:20 по Москве, собирает
недостающие даты по всему периметру, пока не упрётся в лимит, и присылает
сводку админу. Когда бэкфил закончится, тот же прогон каждую неделю докачивает
свежие субботы (одна дата на локацию — копейки по весу).

Часовой лимит здесь не пережидаем (воркер общий): считаем его концом прогона.

Вторая задача — предварительная погода субботним вечером (17:00 МСК): архив
субботу ещё не отдаёт, берём прогнозную модель за сегодня, помечаем строки
source='forecast'; ночной архивный прогон в понедельник их перезапишет.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from app.core.rate_limit import get_redis
from app.core.runtime_env import is_test_run
from app.db.session import get_session_factory
from app.services.admin_telegram_notify import send_admin_report
from app.services.weather_service import (
    ScopeRunSummary,
    collect_event_weather,
    collect_scope,
    event_weather_needed,
    format_run_report,
)
from app.workers.celery_app import celery_app
from app.workers.time_limits import LIMITS_SHORT

logger = logging.getLogger(__name__)

REPORT_TIMEZONE = ZoneInfo("Europe/Moscow")
# Суточный лимит Open-Meteo — 10 000 вызовов, и ночной прогон архива способен
# выгрести его весь: пока идёт пересборка, каждая локация просит свою историю
# целиком. Оставляем запас дню: прогноз на субботу стоит 253 вызова за заход, в
# пятницу заходов три, плюс субботний предварительный прогон. Ночь подождёт до
# завтра, а несобранный прогноз не подождёт — суббота придёт без него.
NIGHTLY_WEIGHT_BUDGET = 8000
# «Сбор завершён» объявляем один раз; потом те же прогоны — еженедельная докачка.
BACKFILL_REPORTED_KEY = "weather:backfill_reported"


@celery_app.task(name="weather.collect_start_weather", time_limit=3 * 3600, soft_time_limit=3 * 3600 - 60)
def collect_start_weather_task() -> dict[str, object]:
    db = get_session_factory()()
    summary = ScopeRunSummary()
    try:
        with httpx.Client(headers={"User-Agent": "run5k.run weather collector"}) as client:
            summary = collect_scope(
                db, client, wait_hourly_reset=False, max_weighted_calls=NIGHTLY_WEIGHT_BUDGET
            )
    except Exception as exc:  # noqa: BLE001 — отчёт важнее трейсбека в логе воркера
        # Сюда попадает только то, что не поймал collect_scope (БД, сеть до первой локации).
        logger.exception("Сбор погоды на стартах упал")
        db.rollback()
        summary.error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        db.close()

    # Тишина, когда докачивать нечего: после бэкфила это будни между субботами.
    if summary.rows_written or summary.stopped_by_limit or summary.error or summary.api_calls:
        redis = get_redis()
        already = bool(redis.get(BACKFILL_REPORTED_KEY))
        send_admin_report(format_run_report(summary, when=datetime.now(REPORT_TIMEZONE), backfill_reported=already))
        if summary.finished and not already:
            redis.set(BACKFILL_REPORTED_KEY, "1")

    return {
        "rows_written": summary.rows_written,
        "api_calls": summary.api_calls,
        "weighted_calls": summary.weighted_calls,
        "locations_complete": summary.locations_complete,
        "scope_locations": summary.scope_locations,
        "stopped_by_limit": summary.stopped_by_limit,
        "stopped_by_budget": summary.stopped_by_budget,
        "finished": summary.finished,
        "error": summary.error,
    }


@celery_app.task(name="weather.collect_preliminary", time_limit=1800, soft_time_limit=1740)
def collect_preliminary_task() -> dict[str, object]:
    db = get_session_factory()()
    summary = ScopeRunSummary(preliminary=True)
    try:
        with httpx.Client(headers={"User-Agent": "run5k.run weather collector"}) as client:
            summary = collect_scope(db, client, wait_hourly_reset=False, preliminary=True)
    except Exception as exc:  # noqa: BLE001 — отчёт важнее трейсбека в логе воркера
        logger.exception("Предварительная погода на стартах упала")
        db.rollback()
        summary.error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        db.close()

    if summary.rows_written or summary.error:
        send_admin_report(format_run_report(summary, when=datetime.now(REPORT_TIMEZONE)))

    return {
        "rows_written": summary.rows_written,
        "api_calls": summary.api_calls,
        "locations_touched": summary.locations_touched,
        "error": summary.error,
    }


@celery_app.task(name="weather.collect_event", **LIMITS_SHORT)
def collect_event_weather_task(location_id: str, event_date: str) -> int:
    """Погода одного свежего старта — ставится при записи нового события."""
    db = get_session_factory()()
    try:
        with httpx.Client(headers={"User-Agent": "run5k.run weather collector"}) as client:
            return collect_event_weather(db, client, UUID(location_id), date.fromisoformat(event_date))
    except Exception:  # noqa: BLE001 — не критично: доберут уведомление или вечерний прогон
        logger.exception("Погода старта %s %s не собрана", location_id, event_date)
        db.rollback()
        return 0
    finally:
        db.close()


# Пауза перед сбором: задача ставится из транзакции синка, и у новой локации
# без закоммиченного события периметр сбора её ещё не видит.
EVENT_WEATHER_COUNTDOWN_SECONDS = 30


def schedule_event_weather(location_id: UUID, event_date: date) -> bool:
    """Поставить сбор погоды свежего старта. Брокер лёг — просто пропускаем:
    погоду доберёт уведомление о пробежке или вечерний прогон."""
    if is_test_run() or not event_weather_needed(event_date):
        return False
    try:
        collect_event_weather_task.apply_async(
            args=[str(location_id), event_date.isoformat()],
            countdown=EVENT_WEATHER_COUNTDOWN_SECONDS,
            queue="celery",
        )
    except Exception:  # noqa: BLE001 — недоступность Redis не должна ронять синк
        logger.exception("Не удалось поставить сбор погоды старта %s %s", location_id, event_date)
        return False
    return True
