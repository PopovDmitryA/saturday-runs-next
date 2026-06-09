from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.sync_dedup_service import (
    _broker_message_matches_user_sync,
    _decode_broker_message_body,
    user_sync_is_pending_in_broker,
)


def test_decode_broker_message_body_base64() -> None:
    import base64

    payload = json.dumps([[str(uuid4()), "manual"], {"platform_link_id": str(uuid4())}, {}])
    envelope = {
        "body": base64.b64encode(payload.encode()).decode(),
        "headers": {"task": "user_sync.run"},
    }
    args, kwargs = _decode_broker_message_body(envelope)
    assert len(args) == 2
    assert "platform_link_id" in kwargs


def test_broker_message_matches_user_sync_by_platform_link_id() -> None:
    user_id = uuid4()
    link_id = uuid4()
    body = json.dumps([[str(user_id), "manual", str(uuid4())], {"platform_link_id": str(link_id)}, {}])
    envelope = {
        "body": body,
        "headers": {"task": "user_sync.run", "id": f"{uuid4()}:five_verst"},
    }
    db = MagicMock()
    assert _broker_message_matches_user_sync(
        json.dumps(envelope),
        user_id=user_id,
        platform_link_id=link_id,
        platform_code="five_verst",
        db=db,
    )


def test_user_sync_is_pending_in_broker_scans_queue() -> None:
    user_id = uuid4()
    link_id = uuid4()
    body = json.dumps([[str(user_id), "manual", str(uuid4())], {"platform_link_id": str(link_id)}, {}])
    envelope = json.dumps(
        {
            "body": body,
            "headers": {"task": "user_sync.run", "id": f"{uuid4()}:five_verst"},
        }
    )
    db = MagicMock()
    with patch("app.services.sync_dedup_service.get_redis_client") as redis_mock:
        redis_mock.return_value.lrange.return_value = [envelope]
        assert user_sync_is_pending_in_broker(
            db,
            user_id=user_id,
            platform_link_id=link_id,
            platform_code="five_verst",
        )
