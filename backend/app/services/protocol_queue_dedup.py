from __future__ import annotations

import json
import re
from datetime import date

from app.services.celery_queue_inspector import (
    FIVE_VERST_BATCH_QUEUE,
    S95_BATCH_QUEUE,
    decode_broker_message_body,
    find_task_queue_position,
    get_celery_task_state,
    get_redis_client,
)

PROTOCOL_FETCH_TASK_BY_PLATFORM: dict[str, str] = {
    "five_verst": "five_verst_sync.fetch_protocol_from_profile",
    "s95": "s95_sync.fetch_protocol_from_profile",
}

BATCH_QUEUE_BY_PLATFORM: dict[str, str] = {
    "five_verst": FIVE_VERST_BATCH_QUEUE,
    "s95": S95_BATCH_QUEUE,
}

_TASK_ID_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def protocol_fetch_event_key(
    platform_code: str,
    location_slug: str,
    event_date: date,
) -> tuple[str, str, str]:
    return platform_code, location_slug, event_date.isoformat()


def protocol_fetch_task_id(
    platform_code: str,
    location_slug: str,
    event_date: date,
) -> str:
    safe_slug = _TASK_ID_SAFE.sub("-", location_slug.strip())[:120]
    return f"protocol-{platform_code}-{safe_slug}-{event_date.isoformat()}"


def _broker_message_matches_protocol_fetch(
    raw: str,
    *,
    platform_code: str,
    location_slug: str,
    event_date_iso: str,
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
    if headers.get("task") != PROTOCOL_FETCH_TASK_BY_PLATFORM.get(platform_code):
        return False
    _args, kwargs = decode_broker_message_body(envelope)
    return (
        kwargs.get("location_slug") == location_slug
        and kwargs.get("event_date_iso") == event_date_iso
    )


def protocol_fetch_pending_in_broker(
    platform_code: str,
    location_slug: str,
    event_date: date,
) -> bool:
    queue_name = BATCH_QUEUE_BY_PLATFORM.get(platform_code)
    task_name = PROTOCOL_FETCH_TASK_BY_PLATFORM.get(platform_code)
    if queue_name is None or task_name is None:
        return False

    event_date_iso = event_date.isoformat()
    task_id = protocol_fetch_task_id(platform_code, location_slug, event_date)
    state = get_celery_task_state(task_id)
    if state in {"STARTED", "RETRY"}:
        return True
    if state == "PENDING" and find_task_queue_position(queue_name, task_id) is not None:
        return True

    redis = get_redis_client()
    for raw in redis.lrange(queue_name, 0, -1):
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if _broker_message_matches_protocol_fetch(
            raw,
            platform_code=platform_code,
            location_slug=location_slug,
            event_date_iso=event_date_iso,
        ):
            return True
    return False


def dedupe_protocol_fetch_queue(
    queue_name: str,
    *,
    platform_code: str,
    dry_run: bool = True,
) -> dict[str, int]:
    """Keep the first pending fetch_protocol_from_profile task per (location, date)."""
    task_name = PROTOCOL_FETCH_TASK_BY_PLATFORM.get(platform_code)
    if task_name is None:
        return {"total": 0, "kept": 0, "removed": 0}

    redis = get_redis_client()
    messages = redis.lrange(queue_name, 0, -1)
    kept: list[bytes | str] = []
    seen: set[tuple[str, str]] = set()
    removed = 0

    for raw in messages:
        raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        try:
            envelope = json.loads(raw_str)
        except json.JSONDecodeError:
            kept.append(raw)
            continue
        if not isinstance(envelope, dict):
            kept.append(raw)
            continue
        headers = envelope.get("headers")
        if not isinstance(headers, dict) or headers.get("task") != task_name:
            kept.append(raw)
            continue
        _args, kwargs = decode_broker_message_body(envelope)
        slug = kwargs.get("location_slug")
        event_date_iso = kwargs.get("event_date_iso")
        if not isinstance(slug, str) or not isinstance(event_date_iso, str):
            kept.append(raw)
            continue
        key = (slug, event_date_iso)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(raw)

    if not dry_run and removed:
        pipe = redis.pipeline()
        pipe.delete(queue_name)
        for item in kept:
            pipe.rpush(queue_name, item)
        pipe.execute()

    return {
        "total": len(messages),
        "kept": len(kept),
        "removed": removed,
    }
