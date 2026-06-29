from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings
from app.platform_adapters.registry import ensure_adapters_registered

settings = get_settings()

celery_app = Celery("saturday_runs", broker=settings.redis_url, backend=settings.redis_url)
ensure_adapters_registered()
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Moscow",
    enable_utc=True,
    task_track_started=True,
    imports=(
        "app.workers.tasks.global_sync",
        "app.workers.tasks.five_verst_sync",
        "app.workers.tasks.user_sync",
        "app.workers.tasks.s95_sync",
        "app.workers.tasks.parkrun_sync",
        "app.workers.tasks.runpark_sync",
    ),
    task_routes={
        "five_verst_sync.*": {"queue": "five_verst"},
        "global_sync.*": {"queue": "five_verst"},
        "user_sync.*": {"queue": "five_verst_user"},
        "s95_sync.run_user_sync": {"queue": "s95_user"},
        "s95_sync.*": {"queue": "s95"},
        "parkrun_sync.*": {"queue": "parkrun"},
        "runpark_sync.*": {"queue": "runpark"},
    },
    beat_schedule={
        "five-verst-registry-daily": {
            "task": "five_verst_sync.sync_locations_registry",
            "schedule": crontab(hour=20, minute=0),
            "options": {"queue": "five_verst"},
        },
        "five-verst-latest-weekday": {
            "task": "five_verst_sync.sync_latest_results",
            "schedule": crontab(hour="0,5,10,15,20", minute=0, day_of_week="1-5"),
            "options": {"queue": "five_verst"},
        },
        "five-verst-latest-saturday-hourly": {
            "task": "five_verst_sync.sync_latest_results",
            "schedule": crontab(hour="1-23", minute=0, day_of_week=6),
            "options": {"queue": "five_verst"},
        },
        "five-verst-latest-sunday-hourly": {
            "task": "five_verst_sync.sync_latest_results",
            "schedule": crontab(hour="0-23", minute=0, day_of_week=0),
            "options": {"queue": "five_verst"},
        },
        "five-verst-location-rotation": {
            "task": "five_verst_sync.sync_location_rotation",
            "schedule": crontab(minute=0, hour="*/4"),
            "options": {"queue": "five_verst"},
        },
        "five-verst-reconcile-protocols": {
            "task": "five_verst_sync.reconcile_stale_protocols",
            "schedule": crontab(minute=0, hour="*/3"),
            "options": {"queue": "five_verst"},
        },
        # S95 location registry — every 3 days at 20:30 MSK via JSON API (s95.ru/by/rs).
        "s95-registry-3days": {
            "task": "s95_sync.sync_locations_registry",
            "schedule": crontab(hour=20, minute=30, day_of_month="*/3"),
            "options": {"queue": "s95"},
        },
        # New protocols scan (JSON API) — Sat & Sun at 11:00 / 17:00 / 23:00 MSK.
        "s95-api-new-protocols-weekend": {
            "task": "s95_sync.api_new_protocols",
            "schedule": crontab(hour="11,17,23", minute=0, day_of_week="6,0"),
            "options": {"queue": "s95"},
        },
        # Reconcile latest Saturday's protocols (late edits) — Mon & Thu night.
        "s95-api-reconcile-latest-mon": {
            "task": "s95_sync.api_reconcile_date",
            "schedule": crontab(hour=3, minute=0, day_of_week=1),
            "kwargs": {"weeks_ago": 0},
            "options": {"queue": "s95"},
        },
        "s95-api-reconcile-latest-thu": {
            "task": "s95_sync.api_reconcile_date",
            "schedule": crontab(hour=3, minute=0, day_of_week=4),
            "kwargs": {"weeks_ago": 0},
            "options": {"queue": "s95"},
        },
        # Reconcile Saturday -1 week — Tue night.
        "s95-api-reconcile-week-1-tue": {
            "task": "s95_sync.api_reconcile_date",
            "schedule": crontab(hour=3, minute=0, day_of_week=2),
            "kwargs": {"weeks_ago": 1},
            "options": {"queue": "s95"},
        },
        # Reconcile Saturday -2 weeks — Wed night.
        "s95-api-reconcile-week-2-wed": {
            "task": "s95_sync.api_reconcile_date",
            "schedule": crontab(hour=3, minute=0, day_of_week=3),
            "kwargs": {"weeks_ago": 2},
            "options": {"queue": "s95"},
        },
        "runpark-latest": {
            "task": "runpark_sync.sync_latest",
            "schedule": crontab(hour="3,8,13,18,23", minute=0),
            "options": {"queue": "runpark"},
        },
    },
)
