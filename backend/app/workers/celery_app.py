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
        "app.workers.tasks.leaderboards_warm",
        "app.workers.tasks.portal_cache",
        "app.workers.tasks.locations_warm",
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
        # Clubs list (/clubs/) — twice a week; changed rows are queued for detail re-sync.
        "five-verst-clubs-registry": {
            "task": "five_verst_sync.sync_clubs_registry",
            "schedule": crontab(hour=21, minute=30, day_of_week="1,4"),
            "options": {"queue": "five_verst"},
        },
        # Club detail rotation — 3×/day, 20 stalest clubs per run (changed ones jump the queue).
        "five-verst-clubs-details": {
            "task": "five_verst_sync.sync_club_details",
            "schedule": crontab(hour="9,15,23", minute=30),
            "options": {"queue": "five_verst"},
        },
        # S95 location registry — every 3 days at 20:30 MSK via JSON API (s95.ru/by/rs).
        "s95-registry-3days": {
            "task": "s95_sync.sync_locations_registry",
            "schedule": crontab(hour=20, minute=30, day_of_month="*/3"),
            "options": {"queue": "s95"},
        },
        # New protocols scan (JSON API, updated_at-aware) — Sat & Sun at 11:00 / 17:00 / 23:00 MSK.
        "s95-api-new-protocols-weekend": {
            "task": "s95_sync.api_new_protocols",
            "schedule": crontab(hour="11,17,23", minute=0, day_of_week="6,0"),
            "options": {"queue": "s95"},
        },
        # Sync new + updated protocols across all locations via updated_at — Mon/Wed/Fri 03:00 MSK.
        "s95-api-sync-updated": {
            "task": "s95_sync.api_sync_updated",
            "schedule": crontab(hour=3, minute=0, day_of_week="1,3,5"),
            "options": {"queue": "s95"},
        },
        # Серверный разбор очереди parkrun (только user-запросы), когда Mac-демон
        # не запущен и охлаждение после капчи истекло. Смещение от :00, чтобы не
        # толкаться с другими задачами.
        "parkrun-pending-queue": {
            "task": "parkrun_sync.process_pending_queue",
            "schedule": crontab(minute="7,37"),
            "options": {"queue": "parkrun"},
        },
        "runpark-latest": {
            "task": "runpark_sync.sync_latest",
            "schedule": crontab(hour="3,8,13,18,23", minute=0),
            "options": {"queue": "runpark"},
        },
        # Прогрев кэша рейтингов (TTL 6ч): каждые 2 часа, со сдвигом от :00,
        # чтобы не толкаться с runpark-latest на том же воркере.
        "leaderboards-warm-cache": {
            "task": "leaderboards.warm_cache",
            "schedule": crontab(minute=20, hour="*/2"),
            "options": {"queue": "runpark"},
        },
        # Прогрев кэша локаций (TTL 3ч): каждые 2 часа, со сдвигом от рейтингов
        # (:20) — чтобы два тяжёлых прогрева не шли одновременно на одном воркере.
        "locations-warm-cache": {
            "task": "locations.warm_cache",
            "schedule": crontab(minute=40, hour="*/2"),
            "options": {"queue": "runpark"},
        },
        # Прогрев Redis-кэша главной портала (TTL 6ч) — раз в 3 часа, чтобы
        # ни один запрос не попадал на холодный пересчёт (~2 мин на проде).
        # Дефолтная очередь "celery" — обслуживает уже существующий сервис
        # worker (без -Q), новых воркеров/очередей заводить не нужно.
        "portal-home-cache-warm": {
            "task": "portal_cache.warm_home",
            "schedule": crontab(hour="*/3", minute=15),
        },
    },
)
