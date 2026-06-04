from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime
from uuid import UUID

from app.core.redis_client import get_redis_client
from app.platform_adapters.canonical import ProfilePreview, ProfilePreviewActivity

PREVIEW_CACHE_PREFIX = "profile_preview:"
PREVIEW_CACHE_TTL_SECONDS = 900


def _cache_key(user_id: UUID, platform_code: str, profile_url: str) -> str:
    normalized = profile_url.strip().lower()
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    return f"{PREVIEW_CACHE_PREFIX}{platform_code}:{user_id}:{digest}"


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _serialize_profile_preview(preview: ProfilePreview) -> str:
    return json.dumps(asdict(preview), ensure_ascii=False, default=_json_default)


def _deserialize_profile_preview(raw: str) -> ProfilePreview:
    data = json.loads(raw)
    activities_raw = data.pop("recent_activities", []) or []
    activities = [
        ProfilePreviewActivity(
            **{
                **item,
                "event_date": date.fromisoformat(item["event_date"])
                if isinstance(item["event_date"], str)
                else item["event_date"],
            }
        )
        for item in activities_raw
    ]
    if isinstance(data.get("data_updated_at"), str):
        data["data_updated_at"] = datetime.fromisoformat(data["data_updated_at"])
    if isinstance(data.get("data_through_date"), str):
        data["data_through_date"] = date.fromisoformat(data["data_through_date"])
    return ProfilePreview(recent_activities=activities, **data)


def store_profile_preview(user_id: UUID, platform_code: str, profile_url: str, preview: ProfilePreview) -> None:
    redis = get_redis_client()
    redis.setex(
        _cache_key(user_id, platform_code, profile_url),
        PREVIEW_CACHE_TTL_SECONDS,
        _serialize_profile_preview(preview),
    )


def get_cached_profile_preview(
    user_id: UUID,
    platform_code: str,
    profile_url: str,
) -> ProfilePreview | None:
    redis = get_redis_client()
    raw = redis.get(_cache_key(user_id, platform_code, profile_url))
    if raw is None:
        return None
    return _deserialize_profile_preview(raw)


def clear_profile_preview_cache(user_id: UUID, platform_code: str, profile_url: str) -> None:
    get_redis_client().delete(_cache_key(user_id, platform_code, profile_url))
