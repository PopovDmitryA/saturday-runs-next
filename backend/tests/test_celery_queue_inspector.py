from __future__ import annotations

import json

from app.services.celery_queue_inspector import extract_task_id_from_broker_message


def test_extract_task_id_from_broker_message() -> None:
    envelope = {
        "body": "W1sibnVsbCJdXQ==",
        "headers": {
            "id": "550e8400-e29b-41d4-a716-446655440000:parkrun",
            "task": "parkrun_sync.run_user_sync",
        },
        "content-type": "application/json",
        "content-encoding": "utf-8",
    }
    raw = json.dumps(envelope)
    assert extract_task_id_from_broker_message(raw) == "550e8400-e29b-41d4-a716-446655440000:parkrun"
