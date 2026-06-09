from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import SyncJob, SyncJobStatus
from app.services.celery_queue_inspector import (
    FIVE_VERST_BATCH_QUEUE,
    S95_BATCH_QUEUE,
    TASK_QUEUE_BY_SUFFIX,
    celery_task_id_for_job,
    decode_broker_message_body,
    find_task_queue_position,
    get_celery_task_state,
    get_redis_client,
)

ACTIVE_SYNC_JOB_STATUSES = frozenset({SyncJobStatus.queued, SyncJobStatus.running})

USER_SYNC_TASK_BY_PLATFORM: dict[str, str] = {
    "five_verst": "user_sync.run",
    "s95": "s95_sync.run_user_sync",
    "parkrun": "parkrun_sync.run_user_sync",
}


@dataclass(frozen=True)
class SyncEnqueueResult:
    job_id: UUID
    duplicate: bool = False


def find_active_sync_job(
    db: Session,
    user_id: UUID,
    platform_link_id: UUID,
) -> SyncJob | None:
    return (
        db.query(SyncJob)
        .filter(
            SyncJob.user_id == user_id,
            SyncJob.platform_link_id == platform_link_id,
            SyncJob.status.in_(tuple(ACTIVE_SYNC_JOB_STATUSES)),
        )
        .order_by(SyncJob.created_at.desc())
        .first()
    )


def _decode_broker_message_body(envelope: dict[str, object]) -> tuple[list[object], dict[str, object]]:
    return decode_broker_message_body(envelope)


def _broker_message_matches_user_sync(
    raw: str,
    *,
    user_id: UUID,
    platform_link_id: UUID,
    platform_code: str,
    db: Session,
) -> bool:
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(envelope, dict):
        return False

    headers = envelope.get("headers")
    if not isinstance(headers, dict):
        return False
    if headers.get("task") != USER_SYNC_TASK_BY_PLATFORM.get(platform_code):
        return False

    args, kwargs = _decode_broker_message_body(envelope)
    if not args or str(args[0]) != str(user_id):
        return False

    link_value = kwargs.get("platform_link_id")
    if link_value is not None:
        return str(link_value) == str(platform_link_id)

    if len(args) > 2 and args[2]:
        try:
            job_id = UUID(str(args[2]))
        except ValueError:
            return False
        job = db.query(SyncJob).filter(SyncJob.id == job_id).one_or_none()
        return job is not None and job.platform_link_id == platform_link_id
    return False


def _queues_for_user_sync(platform_code: str) -> list[str]:
    primary = TASK_QUEUE_BY_SUFFIX[platform_code]
    queues = [primary]
    if platform_code == "five_verst":
        queues.append(FIVE_VERST_BATCH_QUEUE)
    elif platform_code == "s95":
        queues.append(S95_BATCH_QUEUE)
    return queues


def user_sync_is_pending_in_broker(
    db: Session,
    *,
    user_id: UUID,
    platform_link_id: UUID,
    platform_code: str,
) -> bool:
    redis = get_redis_client()
    for queue_name in _queues_for_user_sync(platform_code):
        for raw in redis.lrange(queue_name, 0, -1):
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            if _broker_message_matches_user_sync(
                raw,
                user_id=user_id,
                platform_link_id=platform_link_id,
                platform_code=platform_code,
                db=db,
            ):
                return True
    return False


def celery_task_is_active_for_job(job_id: UUID, platform_code: str) -> bool:
    task_id = celery_task_id_for_job(job_id, platform_code)
    state = get_celery_task_state(task_id)
    if state in {"STARTED", "RETRY"}:
        return True
    if state != "PENDING":
        return False
    for queue_name in _queues_for_user_sync(platform_code):
        if find_task_queue_position(queue_name, task_id) is not None:
            return True
    return False


def user_sync_is_already_pending(
    db: Session,
    *,
    user_id: UUID,
    platform_link_id: UUID,
    platform_code: str,
) -> SyncJob | None:
    """Return an existing job if the same user/platform sync must not be enqueued again."""
    active_job = find_active_sync_job(db, user_id, platform_link_id)
    if active_job is not None and celery_task_is_active_for_job(active_job.id, platform_code):
        return active_job

    if not user_sync_is_pending_in_broker(
        db,
        user_id=user_id,
        platform_link_id=platform_link_id,
        platform_code=platform_code,
    ):
        return None

    if active_job is not None:
        return active_job

    job_id = _job_id_from_broker_queue(
        db,
        user_id=user_id,
        platform_link_id=platform_link_id,
        platform_code=platform_code,
    )
    if job_id is not None:
        existing = db.query(SyncJob).filter(SyncJob.id == job_id).one_or_none()
        if existing is not None:
            return existing

    return active_job


def _job_id_from_broker_queue(
    db: Session,
    *,
    user_id: UUID,
    platform_link_id: UUID,
    platform_code: str,
) -> UUID | None:
    redis = get_redis_client()
    for queue_name in _queues_for_user_sync(platform_code):
        for raw in redis.lrange(queue_name, 0, -1):
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(envelope, dict):
                continue
            headers = envelope.get("headers")
            if not isinstance(headers, dict):
                continue
            if not _broker_message_matches_user_sync(
                raw,
                user_id=user_id,
                platform_link_id=platform_link_id,
                platform_code=platform_code,
                db=db,
            ):
                continue
            args, _kwargs = _decode_broker_message_body(envelope)
            if len(args) > 2 and args[2]:
                try:
                    return UUID(str(args[2]))
                except ValueError:
                    return None
            task_id = headers.get("id")
            if isinstance(task_id, str) and ":" in task_id:
                try:
                    return UUID(task_id.split(":", 1)[0])
                except ValueError:
                    return None
    return None
