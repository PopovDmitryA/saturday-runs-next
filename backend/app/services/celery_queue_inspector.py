from __future__ import annotations

import base64
import json
from typing import Any

from celery.result import AsyncResult

from app.core.redis_client import get_redis_client
from app.workers.celery_app import celery_app

FIVE_VERST_BATCH_QUEUE = "five_verst"
FIVE_VERST_USER_QUEUE = "five_verst_user"
S95_BATCH_QUEUE = "s95"
S95_USER_QUEUE = "s95_user"
PARKRUN_SYNC_QUEUE = "parkrun"

# Backward-compatible alias used by admin summaries for user profile sync.
USER_SYNC_QUEUE = FIVE_VERST_USER_QUEUE
S95_SYNC_QUEUE = S95_USER_QUEUE

TASK_QUEUE_BY_SUFFIX: dict[str, str] = {
    "five_verst": FIVE_VERST_USER_QUEUE,
    "s95": S95_USER_QUEUE,
    "parkrun": PARKRUN_SYNC_QUEUE,
}


def celery_task_id_for_job(job_id: object, suffix: str) -> str:
    return f"{job_id}:{suffix}"


def _decode_headers(raw_headers: Any) -> dict[str, Any]:
    if isinstance(raw_headers, dict):
        return raw_headers
    if isinstance(raw_headers, str):
        try:
            return json.loads(raw_headers)
        except json.JSONDecodeError:
            return {}
    return {}


def extract_task_id_from_broker_message(raw: str) -> str | None:
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return None
    headers = _decode_headers(envelope.get("headers"))
    task_id = headers.get("id")
    return str(task_id) if task_id else None


def decode_broker_message_body(envelope: dict[str, object]) -> tuple[list[object], dict[str, object]]:
    body = envelope.get("body")
    if body is None:
        return [], {}
    payload: object
    if isinstance(body, str):
        try:
            payload = json.loads(base64.b64decode(body))
        except (json.JSONDecodeError, ValueError):
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return [], {}
    else:
        payload = body
    if not isinstance(payload, list) or len(payload) < 2:
        return [], {}
    args = payload[0] if isinstance(payload[0], list) else []
    kwargs = payload[1] if isinstance(payload[1], dict) else {}
    return args, kwargs


def get_queue_length(queue_name: str) -> int:
    return int(get_redis_client().llen(queue_name) or 0)


def find_task_queue_position(queue_name: str, task_id: str) -> int | None:
    """1-based position in the broker queue, or None if the task is not pending there."""
    redis = get_redis_client()
    messages = redis.lrange(queue_name, 0, -1)
    for index, raw in enumerate(messages):
        if extract_task_id_from_broker_message(raw) == task_id:
            return index + 1
    return None


def get_celery_task_state(task_id: str) -> str:
    return AsyncResult(task_id, app=celery_app).state


def inspect_user_task(
    job_id: object,
    suffix: str,
) -> dict[str, object] | None:
    task_id = celery_task_id_for_job(job_id, suffix)
    queue_name = TASK_QUEUE_BY_SUFFIX[suffix]
    state = get_celery_task_state(task_id)
    if state in {"PENDING", "STARTED", "RETRY"}:
        position = find_task_queue_position(queue_name, task_id) if state == "PENDING" else None
        return {
            "celery_task_id": task_id,
            "suffix": suffix,
            "queue": queue_name,
            "celery_state": state,
            "queue_position": position,
            "queue_length": get_queue_length(queue_name),
        }
    if state in {"SUCCESS", "FAILURE", "REVOKED"}:
        return {
            "celery_task_id": task_id,
            "suffix": suffix,
            "queue": queue_name,
            "celery_state": state,
            "queue_position": None,
            "queue_length": get_queue_length(queue_name),
        }
    return None
