from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Platform, ProtocolSyncState
from app.platform_adapters.canonical import CanonicalRunResult
from app.sync.profile_protocol_queue import (
    collect_profile_event_refs,
    enqueue_missing_protocols_for_profile,
    is_protocol_fully_loaded,
)


def _run(**kwargs: object) -> CanonicalRunResult:
    defaults = {
        "external_user_id": "1",
        "participant_name": "A",
        "location_external_key": "bitsa",
        "location_name": "Битца",
        "event_date": date(2026, 6, 6),
        "event_number": 205,
        "external_result_key": "1:2026-06-06:bitsa",
        "position": 1,
        "finish_time_sec": 1800,
        "finish_time_display": "00:30:00",
    }
    defaults.update(kwargs)
    return CanonicalRunResult(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if row is None:
        pytest.skip("five_verst platform not seeded")
    return row


def test_collect_profile_event_refs_dedupes_and_limits() -> None:
    runs = [
        _run(),
        _run(),
        _run(
            location_external_key="zil",
            location_name="ЗИЛ",
            event_date=date(2026, 5, 30),
            event_number=100,
            external_result_key="1:2026-05-30:zil",
        ),
    ]
    refs = collect_profile_event_refs(runs, [], platform_code="five_verst", limit=10)
    assert len(refs) == 2
    assert refs[0].location_slug == "bitsa"
    assert refs[1].location_slug == "zil"


def test_is_protocol_fully_loaded_requires_protocol_sync_state(
    db_session: Session,
    platform: Platform,
) -> None:
    slug = f"testpark-{uuid4().hex[:8]}"
    location = Location(
        id=uuid4(),
        platform_id=platform.id,
        external_key=slug,
        name="Test Park",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    event = Event(
        id=uuid4(),
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{slug}:2026-06-06",
        event_date=date(2026, 6, 6),
        title="Test Park",
    )
    db_session.add(event)
    db_session.flush()

    assert (
        is_protocol_fully_loaded(
            db_session,
            platform,
            location_slug=slug,
            event_date=date(2026, 6, 6),
        )
        is False
    )

    db_session.add(ProtocolSyncState(event_id=event.id))
    db_session.flush()
    assert (
        is_protocol_fully_loaded(
            db_session,
            platform,
            location_slug=slug,
            event_date=date(2026, 6, 6),
        )
        is False
    )

    state = db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()
    state.last_protocol_fetched_at = datetime.now(timezone.utc)
    db_session.flush()
    assert (
        is_protocol_fully_loaded(
            db_session,
            platform,
            location_slug=slug,
            event_date=date(2026, 6, 6),
        )
        is True
    )


@patch("app.sync.profile_protocol_queue.batch_queue_has_capacity", return_value=True)
@patch("app.sync.profile_protocol_queue.protocol_fetch_pending_in_broker", return_value=False)
@patch("app.workers.tasks.five_verst_sync.fetch_protocol_from_profile_task.apply_async")
def test_enqueue_missing_protocols_for_profile_five_verst(
    apply_async_mock: MagicMock,
    _pending_mock: MagicMock,
    _capacity_mock: MagicMock,
    db_session: Session,
    platform: Platform,
) -> None:
    runs = [_run(external_user_id="42", external_result_key="42:2026-06-06:bitsa")]
    result = enqueue_missing_protocols_for_profile(
        db_session,
        platform,
        runs,
        [],
        limit=5,
    )
    assert result.checked == 1
    assert result.missing == 1
    assert result.enqueued == 1
    apply_async_mock.assert_called_once()
    assert apply_async_mock.call_args.kwargs["task_id"].startswith("protocol-five_verst-bitsa-")


@patch("app.sync.profile_protocol_queue.protocol_fetch_pending_in_broker", return_value=True)
@patch("app.workers.tasks.five_verst_sync.fetch_protocol_from_profile_task.apply_async")
def test_enqueue_skips_duplicate_protocol_fetch(
    apply_async_mock: MagicMock,
    _pending_mock: MagicMock,
    db_session: Session,
    platform: Platform,
) -> None:
    runs = [_run(external_user_id="42", external_result_key="42:2026-06-06:bitsa")]
    result = enqueue_missing_protocols_for_profile(
        db_session,
        platform,
        runs,
        [],
        limit=5,
    )
    assert result.missing == 1
    assert result.enqueued == 0
    assert result.skipped_duplicate == 1
    apply_async_mock.assert_not_called()
