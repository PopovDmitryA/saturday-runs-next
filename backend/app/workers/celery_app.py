from __future__ import annotations

from celery import Celery

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
        "s95_sync.*": {"queue": "s95"},
        "parkrun_sync.*": {"queue": "parkrun"},
    },
    # Расписание отключено до деплоя на сервер и согласования cron (см. docs/five_verst_sync_plan.md).
    beat_schedule={},
)
