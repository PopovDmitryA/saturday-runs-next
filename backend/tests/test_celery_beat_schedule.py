from __future__ import annotations

from app.workers.celery_app import celery_app


def test_s95_beat_schedule_offset_from_five_verst() -> None:
    schedule = celery_app.conf.beat_schedule
    assert schedule["s95-registry-daily"]["schedule"].minute == {30}
    assert schedule["five-verst-registry-daily"]["schedule"].minute == {0}
    assert schedule["five-verst-latest-weekday"]["schedule"].hour == {0, 5, 10, 15, 20}
    assert schedule["five-verst-latest-weekday"]["schedule"].day_of_week == {1, 2, 3, 4, 5}
    assert schedule["s95-latest-weekday"]["schedule"].hour == {0, 5, 10, 15, 20}
    assert schedule["s95-latest-weekday"]["schedule"].minute == {30}
    assert schedule["s95-athletes-registry"]["task"] == "s95_sync.sync_athletes_registry"
    assert schedule["s95-athletes-registry"]["schedule"].hour == set(range(0, 24, 2))
    assert schedule["s95-athletes-registry"]["schedule"].minute == {30}
