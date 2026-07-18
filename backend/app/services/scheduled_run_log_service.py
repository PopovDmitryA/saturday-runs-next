"""История запусков задач автообновления (celery beat).

Каждый запуск, проходящий через `run_reported_sync`, пишется в
`scheduled_run_logs`: что запускали, сколько шло, что обновилось, какие были
ошибки. Админка «Автообновление» листает эту историю, а
`admin_digest.daily_sync_summary` раз в сутки собирает по ней сводку для ВК.

Детали по локациям (DETAIL_LIST_KEYS) не сохраняются: в истории нужны итоги,
а не перечисление площадок.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.models import ScheduledRunLog
from app.services.sync_report_labels import DETAIL_LIST_KEYS, field_label, pipeline_label

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

PLATFORM_LABELS: dict[str, str] = {
    "five_verst": "5 вёрст",
    "s95": "S95",
    "parkrun": "parkrun",
    "runpark": "RunPark",
    "other": "Прочее",
}

# Пайплайны именуются человекочитаемо («5v latest /results/latest/»), платформу
# определяем по префиксу имени — см. PIPELINE_LABELS в sync_report_labels.
_PLATFORM_PREFIXES: tuple[tuple[str, str], ...] = (
    ("5v ", "five_verst"),
    ("s95 ", "s95"),
    ("parkrun", "parkrun"),
    ("runpark", "runpark"),
)

# «Главная цифра» запуска для колонки «Обновлено»: берём первый присутствующий
# ключ. Порядок — от самого содержательного к самому общему, чтобы у забега
# показывались записанные результаты, а у реестра — обновлённые локации.
_HEADLINE_KEYS: tuple[str, ...] = (
    "run_results_upserted",
    "participants_synced",
    "runs_imported",
    "protocols_created",
    "protocols_updated",
    "protocols_changed",
    "protocols_fetched",
    "summaries_upserted",
    "events_upserted",
    "locations_updated",
    "locations_created",
    "location_upserted",
    "clubs_upserted",
    "enqueued",
)

MAX_STORED_ERRORS = 20
MAX_ERROR_LENGTH = 500
DEFAULT_RETENTION_DAYS = 120
DEFAULT_PERIOD_DAYS = 7

# Дни в админке и в сводке считаем по московскому времени — как и расписание beat.
RUNS_TIMEZONE = ZoneInfo("Europe/Moscow")


def resolve_period(
    *,
    period_days: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[datetime, datetime]:
    """Границы периода (МСК). Явные даты приоритетнее period_days; `date_to` включительно."""
    today = datetime.now(RUNS_TIMEZONE).date()
    if date_from or date_to:
        start_day = date_from or (date_to - timedelta(days=DEFAULT_PERIOD_DAYS - 1))
        end_day = date_to or today
    else:
        days = period_days or DEFAULT_PERIOD_DAYS
        end_day = today
        start_day = today - timedelta(days=days - 1)
    if start_day > end_day:
        start_day = end_day
    start = datetime.combine(start_day, time.min, tzinfo=RUNS_TIMEZONE)
    end = datetime.combine(end_day, time.min, tzinfo=RUNS_TIMEZONE) + timedelta(days=1)
    return start, end


def platform_for_pipeline(pipeline: str) -> str:
    lowered = pipeline.lower()
    for prefix, platform in _PLATFORM_PREFIXES:
        if lowered.startswith(prefix):
            return platform
    return "other"


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def _clean_errors(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("errors")
    if not isinstance(raw, list):
        return []
    return [str(item)[:MAX_ERROR_LENGTH] for item in raw]


def extract_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    """Скалярные итоги запуска: без списков деталей и без самих ошибок."""
    metrics: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"errors", "skipped", "reason"} or key in DETAIL_LIST_KEYS:
            continue
        if value is None:
            continue
        if isinstance(value, (int, float, str, bool)):
            metrics[key] = value
        elif isinstance(value, list):
            metrics[key] = len(value)
    return metrics


def headline_count(metrics: dict[str, Any]) -> int:
    for key in _HEADLINE_KEYS:
        value = metrics.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def record_run(
    pipeline: str,
    payload: dict[str, Any],
    *,
    started_at: datetime,
    finished_at: datetime,
    failed: bool = False,
) -> None:
    """Пишет запуск в историю. Никогда не роняет саму задачу."""
    errors = _clean_errors(payload)
    metrics = extract_metrics(payload)
    skipped = bool(payload.get("skipped"))

    if skipped:
        status = STATUS_SKIPPED
    elif failed:
        status = STATUS_FAILED
    elif errors:
        status = STATUS_ERROR
    else:
        status = STATUS_OK

    entry = ScheduledRunLog(
        pipeline=pipeline[:128],
        platform=platform_for_pipeline(pipeline),
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=max(int((finished_at - started_at).total_seconds() * 1000), 0),
        records_updated=0 if skipped else headline_count(metrics),
        error_count=len(errors),
        skip_reason=str(payload.get("reason"))[:64] if skipped and payload.get("reason") else None,
        metrics=metrics or None,
        errors=errors[:MAX_STORED_ERRORS] or None,
    )

    db = get_session_factory()()
    try:
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Не удалось записать запуск %s в историю автообновления", pipeline)
    finally:
        db.close()


def _serialize(entry: ScheduledRunLog) -> dict[str, Any]:
    metrics = entry.metrics or {}
    return {
        "id": entry.id,
        "pipeline": entry.pipeline,
        "pipeline_label": pipeline_label(entry.pipeline),
        "platform": entry.platform,
        "platform_label": platform_label(entry.platform),
        "status": entry.status,
        "started_at": entry.started_at,
        "finished_at": entry.finished_at,
        "duration_ms": entry.duration_ms,
        "records_updated": entry.records_updated,
        "error_count": entry.error_count,
        "skip_reason": entry.skip_reason,
        "metrics": [
            {"key": key, "label": field_label(key), "value": value}
            for key, value in metrics.items()
        ],
        "errors": list(entry.errors or []),
    }


def list_runs(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    platform: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    filters = [ScheduledRunLog.started_at >= start, ScheduledRunLog.started_at < end]
    if platform:
        filters.append(ScheduledRunLog.platform == platform)
    if status == "problems":
        filters.append(ScheduledRunLog.status.in_([STATUS_ERROR, STATUS_FAILED]))
    elif status:
        filters.append(ScheduledRunLog.status == status)

    total = db.execute(select(func.count()).select_from(ScheduledRunLog).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(ScheduledRunLog)
            .where(*filters)
            .order_by(ScheduledRunLog.started_at.desc(), ScheduledRunLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return [_serialize(row) for row in rows], int(total)


def summarize(db: Session, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Итоги периода: по платформам, внутри — по пайплайнам.

    Метрики суммируются по всем числовым ключам запуска, поэтому сводка
    отвечает на «сколько раз запускали и что обновилось» без деталей локаций.
    """
    rows = (
        db.execute(
            select(ScheduledRunLog)
            .where(ScheduledRunLog.started_at >= start, ScheduledRunLog.started_at < end)
            .order_by(ScheduledRunLog.started_at)
        )
        .scalars()
        .all()
    )

    platforms: dict[str, dict[str, Any]] = {}
    for row in rows:
        platform = platforms.setdefault(
            row.platform,
            {
                "platform": row.platform,
                "platform_label": platform_label(row.platform),
                "runs": 0,
                "ok": 0,
                "problems": 0,
                "skipped": 0,
                "errors_total": 0,
                "metrics": {},
                "pipelines": {},
            },
        )
        pipeline = platform["pipelines"].setdefault(
            row.pipeline,
            {
                "pipeline": row.pipeline,
                "pipeline_label": pipeline_label(row.pipeline),
                "runs": 0,
                "ok": 0,
                "problems": 0,
                "skipped": 0,
                "errors_total": 0,
                "metrics": {},
                "last_run_at": None,
                "last_status": None,
            },
        )

        for bucket in (platform, pipeline):
            bucket["runs"] += 1
            bucket["errors_total"] += row.error_count
            if row.status == STATUS_SKIPPED:
                bucket["skipped"] += 1
            elif row.status == STATUS_OK:
                bucket["ok"] += 1
            else:
                bucket["problems"] += 1
            for key, value in (row.metrics or {}).items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                bucket["metrics"][key] = bucket["metrics"].get(key, 0) + value

        pipeline["last_run_at"] = row.started_at
        pipeline["last_status"] = row.status

    return [_finalize_platform(platform) for platform in _sorted_platforms(platforms.values())]


def _sorted_platforms(platforms: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {name: index for index, name in enumerate(PLATFORM_LABELS)}
    return sorted(platforms, key=lambda item: order.get(item["platform"], len(order)))


def _labeled_metrics(metrics: dict[str, float]) -> list[dict[str, Any]]:
    """Только содержательные счётчики: нули и служебные индексы не показываем."""
    items = [
        {"key": key, "label": field_label(key), "value": int(value) if float(value).is_integer() else value}
        for key, value in metrics.items()
        if value and key not in {"rotation_index", "locations_total"}
    ]
    items.sort(key=lambda item: item["value"], reverse=True)
    return items


def _finalize_platform(platform: dict[str, Any]) -> dict[str, Any]:
    pipelines = sorted(
        platform["pipelines"].values(),
        key=lambda item: (-item["problems"], -item["runs"], item["pipeline_label"]),
    )
    for pipeline in pipelines:
        pipeline["metrics"] = _labeled_metrics(pipeline["metrics"])
    platform["pipelines"] = pipelines
    platform["metrics"] = _labeled_metrics(platform["metrics"])
    return platform


def recent_problem_runs(db: Session, *, start: datetime, end: datetime, limit: int = 5) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            select(ScheduledRunLog)
            .where(
                ScheduledRunLog.started_at >= start,
                ScheduledRunLog.started_at < end,
                ScheduledRunLog.status.in_([STATUS_ERROR, STATUS_FAILED]),
            )
            .order_by(ScheduledRunLog.started_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [_serialize(row) for row in rows]


def cleanup_old_runs(db: Session, *, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = db.execute(delete(ScheduledRunLog).where(ScheduledRunLog.started_at < cutoff))
    db.commit()
    return int(result.rowcount or 0)
