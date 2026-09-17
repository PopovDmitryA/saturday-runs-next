"""Ежедневный прогноз погоды на ближайший старт.

Идея Дмитрия 17.09.2026: человек выбирает, куда поехать в субботу, и заодно
хочет знать, что там будет с погодой. Задача раз в сутки спрашивает прогноз
по каждой локации периметра (один вызов на локацию — около 4% суточного
лимита Open-Meteo) и обновляет строку ближайшей субботы.

Идёт перед архивным прогоном: тот выгребает остаток лимита на пересборку
истории, и прогноз должен успеть взять своё.
"""

from __future__ import annotations

import logging

import httpx

from app.db.session import get_session_factory
from app.services.admin_telegram_notify import send_admin_report
from app.services.weather_forecast_service import ForecastStats, collect_forecasts, next_start_date
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="weather.collect_forecast", time_limit=3600, soft_time_limit=3540)
def collect_forecast_task() -> dict[str, object]:
    from datetime import date

    db = get_session_factory()()
    stats = ForecastStats()
    error = ""
    try:
        with httpx.Client(headers={"User-Agent": "run5k.run weather forecast"}) as client:
            stats = collect_forecasts(db, client)
    except Exception as exc:  # noqa: BLE001 — отчёт важнее трейсбека в логе воркера
        logger.exception("Прогноз погоды на старты упал")
        db.rollback()
        error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        db.close()

    target = next_start_date(date.today())
    # Молчим в обычный день: отчёт нужен, только когда прогноз собрался не весь.
    if error or stats.skipped or stats.rows_written < stats.locations:
        send_admin_report(
            f"🌤 Прогноз на {target.strftime('%d.%m')}: {stats.rows_written} из {stats.locations} локаций, "
            f"вызовов {stats.api_calls}" + (f"\n⚠️ {error}" if error else "")
        )
    return {
        "target_date": target.isoformat(),
        "locations": stats.locations,
        "rows_written": stats.rows_written,
        "api_calls": stats.api_calls,
        "skipped": stats.skipped,
        "error": error,
    }
