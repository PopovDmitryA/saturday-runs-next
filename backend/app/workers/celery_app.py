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
    ),
    task_routes={
        "five_verst_sync.*": {"queue": "five_verst"},
        "global_sync.*": {"queue": "five_verst"},
        "user_sync.*": {"queue": "five_verst"},
        "s95_sync.*": {"queue": "s95"},
        "parkrun_sync.*": {"queue": "parkrun"},
    },
    beat_schedule={
        "five-verst-registry-daily": {
            "task": "five_verst_sync.sync_locations_registry",
            "schedule": crontab(hour=20, minute=0),
            "options": {"queue": "five_verst"},
        },
        "five-verst-latest-weekday-morning": {
            "task": "five_verst_sync.sync_latest_results",
            "schedule": crontab(hour=5, minute=0, day_of_week="1-5"),
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
        "s95-registry-daily": {
            "task": "s95_sync.sync_locations_registry",
            "schedule": crontab(hour=20, minute=30),
            "options": {"queue": "s95"},
        },
        "s95-latest-weekday-morning": {
            "task": "s95_sync.sync_latest",
            "schedule": crontab(hour=5, minute=30, day_of_week="1-5"),
            "options": {"queue": "s95"},
        },
        "s95-latest-saturday-hourly": {
            "task": "s95_sync.sync_latest",
            "schedule": crontab(hour="1-23", minute=30, day_of_week=6),
            "options": {"queue": "s95"},
        },
        "s95-latest-sunday-hourly": {
            "task": "s95_sync.sync_latest",
            "schedule": crontab(hour="0-23", minute=30, day_of_week=0),
            "options": {"queue": "s95"},
        },
        "s95-location-rotation": {
            "task": "s95_sync.sync_location_rotation",
            "schedule": crontab(minute=30, hour="*/4"),
            "options": {"queue": "s95"},
        },
        "s95-reconcile-protocols": {
            "task": "s95_sync.reconcile_stale_protocols",
            "schedule": crontab(minute=30, hour="*/3"),
            "options": {"queue": "s95"},
        },
        "s95-athletes-registry": {
            "task": "s95_sync.sync_athletes_registry",
            "schedule": crontab(minute=30, hour="*/2"),
            "options": {"queue": "s95"},
        },
    },
)
