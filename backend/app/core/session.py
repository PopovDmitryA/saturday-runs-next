from __future__ import annotations

import json
from datetime import datetime, timezone

from app.config import Settings
from app.core.redis_client import get_redis_client
from app.core.security import generate_session_id, sign_session_id, verify_signed_session_id

SESSION_KEY_PREFIX = "session:"


def _session_key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}"


def create_session(settings: Settings, user_id: str) -> str:
    client = get_redis_client()
    session_id = generate_session_id()
    payload = json.dumps({"user_id": user_id, "created_at": datetime.now(timezone.utc).isoformat()})
    client.setex(_session_key(session_id), settings.session_ttl_seconds, payload)
    return sign_session_id(session_id, settings.app_secret_key)


def get_session_user_id(settings: Settings, signed_cookie: str) -> str | None:
    session_id = verify_signed_session_id(signed_cookie, settings.app_secret_key)
    if session_id is None:
        return None

    client = get_redis_client()
    raw = client.get(_session_key(session_id))
    if raw is None:
        return None

    data = json.loads(raw)
    return str(data["user_id"])


def delete_session(settings: Settings, signed_cookie: str) -> None:
    session_id = verify_signed_session_id(signed_cookie, settings.app_secret_key)
    if session_id is None:
        return
    get_redis_client().delete(_session_key(session_id))
