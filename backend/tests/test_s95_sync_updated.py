from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Platform, ProtocolSyncState, SyncWatermark
from app.s95.api_client import S95ApiActivityRef, S95ApiLocation
from app.services.sync_watermark_service import S95_PROTOCOLS_RECONCILED_THROUGH
from app.sync.s95_global_sync_api import sync_updated_protocols
from app.sync.s95_protocol_api import upsert_activity_protocol_api

# Same trimmed shape as test_s95_api_protocol.py's ACTIVITY_JSON (Пенза, 26.10.2024).
ACTIVITY_JSON = {
    "date": "26.10.2024",
    "event": {"name": "Пенза", "code_name": "penza", "town": "Пенза"},
    "results": [
        {"total_time": "24:27", "position": 1, "athlete": {"id": 15512, "name": "Наталия МАШТАКОВА", "gender": "female"}},
    ],
    "volunteers": [],
}

# Same protocol, genuinely different result content (for "server-side edit" scenarios —
# a bump in updated_at with matching content should hit the unchanged fast-path instead).
ACTIVITY_JSON_EDITED = {
    "date": "26.10.2024",
    "event": {"name": "Пенза", "code_name": "penza", "town": "Пенза"},
    "results": [
        {"total_time": "24:15", "position": 1, "athlete": {"id": 15512, "name": "Наталия МАШТАКОВА", "gender": "female"}},
    ],
    "volunteers": [],
}


def _flush_instead_of_commit(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """`sync_updated_protocols` commits after each step (crash-safety in production). The
    `db_session` fixture wraps each test in a SAVEPOINT via a bare `session.begin_nested()`
    restarted by an `after_transaction_end` listener; interleaving that with a later
    `with db.begin_nested()` (used deeper in the upsert chain for real Event creation)
    trips a SQLAlchemy `_trans_context_manager` staleness that doesn't occur against a real,
    non-nested production session. Swap commit for flush so writes are still visible to
    same-session queries without exercising that test-harness-only interaction.
    """
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def _api_location(slug: str) -> S95ApiLocation:
    return S95ApiLocation(domain="https://s95.ru", slug=slug, name="Пенза", town="Пенза", place="", active=True)


def _make_location(db_session: Session, platform: Platform, slug: str) -> Location:
    loc = Location(
        platform_id=platform.id,
        external_key=slug,
        name="Пенза",
        source_url=f"https://s95.ru/events/{slug}",
    )
    db_session.add(loc)
    db_session.flush()
    return loc


def test_sync_updated_fetches_new_protocol(
    db_session: Session, s95_platform: Platform, monkeypatch: pytest.MonkeyPatch
):
    _flush_instead_of_commit(db_session, monkeypatch)
    slug = f"penza-{uuid4().hex[:6]}"
    ref = S95ApiActivityRef(
        date="2024-10-26", url="https://s95.ru/activities/1855.json", updated_at="2026-01-01T00:00:00+03:00"
    )

    with patch("app.sync.s95_global_sync_api.fetch_all_locations", return_value=[_api_location(slug)]), \
         patch("app.sync.s95_global_sync_api.fetch_event_activities", return_value=[ref]), \
         patch("app.sync.s95_protocol_api.fetch_activity", return_value=ACTIVITY_JSON):
        result = sync_updated_protocols(db_session, delay_min_sec=0, delay_max_sec=0)

    assert not result.errors
    assert result.protocols_created == 1

    event = db_session.query(Event).filter(
        Event.platform_id == s95_platform.id, Event.external_event_key == f"{slug}:2024-10-26"
    ).one()
    state = db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()
    assert state.source_updated_at == ref.updated_at_dt()


def test_sync_updated_skips_when_updated_at_not_newer(db_session: Session, s95_platform: Platform):
    slug = f"penza-{uuid4().hex[:6]}"
    location = _make_location(db_session, s95_platform, slug)
    seed_ref = S95ApiActivityRef(
        date="2024-10-26", url="https://s95.ru/activities/1855.json", updated_at="2026-01-01T00:00:00+03:00"
    )
    upsert_activity_protocol_api(db_session, s95_platform, location, seed_ref, activity_json=ACTIVITY_JSON)
    db_session.flush()

    same_ref = S95ApiActivityRef(
        date="2024-10-26", url="https://s95.ru/activities/1855.json", updated_at="2026-01-01T00:00:00+03:00"
    )
    with patch("app.sync.s95_global_sync_api.fetch_all_locations", return_value=[_api_location(slug)]), \
         patch("app.sync.s95_global_sync_api.fetch_event_activities", return_value=[same_ref]), \
         patch("app.sync.s95_protocol_api.fetch_activity") as mock_fetch:
        result = sync_updated_protocols(db_session, delay_min_sec=0, delay_max_sec=0)

    mock_fetch.assert_not_called()
    assert not result.errors
    assert result.protocols_unchanged == 1
    assert result.protocols_created == 0
    assert result.protocols_updated == 0


def test_sync_updated_refetches_when_updated_at_newer(
    db_session: Session, s95_platform: Platform, monkeypatch: pytest.MonkeyPatch
):
    slug = f"penza-{uuid4().hex[:6]}"
    location = _make_location(db_session, s95_platform, slug)
    seed_ref = S95ApiActivityRef(
        date="2024-10-26", url="https://s95.ru/activities/1855.json", updated_at="2026-01-01T00:00:00+03:00"
    )
    upsert_activity_protocol_api(db_session, s95_platform, location, seed_ref, activity_json=ACTIVITY_JSON)
    db_session.flush()
    _flush_instead_of_commit(db_session, monkeypatch)

    # Content genuinely changed (not just a cosmetic updated_at bump) — must refetch and rewrite.
    newer_ref = S95ApiActivityRef(
        date="2024-10-26", url="https://s95.ru/activities/1855.json", updated_at="2026-02-01T00:00:00+03:00"
    )
    with patch("app.sync.s95_global_sync_api.fetch_all_locations", return_value=[_api_location(slug)]), \
         patch("app.sync.s95_global_sync_api.fetch_event_activities", return_value=[newer_ref]), \
         patch("app.sync.s95_protocol_api.fetch_activity", return_value=ACTIVITY_JSON_EDITED) as mock_fetch:
        result = sync_updated_protocols(db_session, delay_min_sec=0, delay_max_sec=0)

    mock_fetch.assert_called_once()
    assert not result.errors
    assert result.protocols_updated == 1
    assert result.protocols_unchanged == 0


def test_sync_updated_seeds_null_source_updated_at_without_fetch(db_session: Session, s95_platform: Platform):
    slug = f"penza-{uuid4().hex[:6]}"
    location = _make_location(db_session, s95_platform, slug)
    # Simulate a protocol synced before this column existed: updated_at absent at fetch time.
    legacy_ref = S95ApiActivityRef(date="2024-10-26", url="https://s95.ru/activities/1855.json", updated_at=None)
    upsert_activity_protocol_api(db_session, s95_platform, location, legacy_ref, activity_json=ACTIVITY_JSON)
    db_session.flush()

    event = db_session.query(Event).filter(
        Event.platform_id == s95_platform.id, Event.external_event_key == f"{slug}:2024-10-26"
    ).one()
    state = db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()
    assert state.source_updated_at is None
    last_fetched_at = state.last_protocol_fetched_at

    older_ref = S95ApiActivityRef(
        date="2024-10-26",
        url="https://s95.ru/activities/1855.json",
        updated_at=(last_fetched_at - timedelta(days=1)).isoformat(),
    )
    with patch("app.sync.s95_global_sync_api.fetch_all_locations", return_value=[_api_location(slug)]), \
         patch("app.sync.s95_global_sync_api.fetch_event_activities", return_value=[older_ref]), \
         patch("app.sync.s95_protocol_api.fetch_activity") as mock_fetch:
        result = sync_updated_protocols(db_session, delay_min_sec=0, delay_max_sec=0)

    mock_fetch.assert_not_called()
    assert not result.errors
    assert result.protocols_unchanged == 1
    db_session.refresh(state)
    assert state.source_updated_at == older_ref.updated_at_dt()


def test_sync_updated_fetches_when_null_source_updated_at_and_newer_than_last_fetch(
    db_session: Session, s95_platform: Platform, monkeypatch: pytest.MonkeyPatch
):
    slug = f"penza-{uuid4().hex[:6]}"
    location = _make_location(db_session, s95_platform, slug)
    legacy_ref = S95ApiActivityRef(date="2024-10-26", url="https://s95.ru/activities/1855.json", updated_at=None)
    upsert_activity_protocol_api(db_session, s95_platform, location, legacy_ref, activity_json=ACTIVITY_JSON)
    db_session.flush()
    _flush_instead_of_commit(db_session, monkeypatch)

    event = db_session.query(Event).filter(
        Event.platform_id == s95_platform.id, Event.external_event_key == f"{slug}:2024-10-26"
    ).one()
    state = db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()
    last_fetched_at = state.last_protocol_fetched_at

    # Content genuinely changed (not just a cosmetic updated_at bump) — must refetch and rewrite.
    newer_ref = S95ApiActivityRef(
        date="2024-10-26",
        url="https://s95.ru/activities/1855.json",
        updated_at=(last_fetched_at + timedelta(days=1)).isoformat(),
    )
    with patch("app.sync.s95_global_sync_api.fetch_all_locations", return_value=[_api_location(slug)]), \
         patch("app.sync.s95_global_sync_api.fetch_event_activities", return_value=[newer_ref]), \
         patch("app.sync.s95_protocol_api.fetch_activity", return_value=ACTIVITY_JSON_EDITED) as mock_fetch:
        result = sync_updated_protocols(db_session, delay_min_sec=0, delay_max_sec=0)

    mock_fetch.assert_called_once()
    assert not result.errors
    assert result.protocols_updated == 1


def test_sync_updated_sets_watermark_on_success(
    db_session: Session, s95_platform: Platform, monkeypatch: pytest.MonkeyPatch
):
    _flush_instead_of_commit(db_session, monkeypatch)
    slug = f"penza-{uuid4().hex[:6]}"
    ref = S95ApiActivityRef(
        date="2024-10-26", url="https://s95.ru/activities/1855.json", updated_at="2026-01-01T00:00:00+03:00"
    )
    with patch("app.sync.s95_global_sync_api.fetch_all_locations", return_value=[_api_location(slug)]), \
         patch("app.sync.s95_global_sync_api.fetch_event_activities", return_value=[ref]), \
         patch("app.sync.s95_protocol_api.fetch_activity", return_value=ACTIVITY_JSON):
        sync_updated_protocols(db_session, delay_min_sec=0, delay_max_sec=0)

    watermark = db_session.query(SyncWatermark).filter(
        SyncWatermark.platform_id == s95_platform.id,
        SyncWatermark.key == S95_PROTOCOLS_RECONCILED_THROUGH,
    ).one_or_none()
    assert watermark is not None


def test_sync_updated_skips_watermark_on_error(db_session: Session, s95_platform: Platform):
    slug = f"penza-{uuid4().hex[:6]}"
    with patch("app.sync.s95_global_sync_api.fetch_all_locations", return_value=[_api_location(slug)]), \
         patch("app.sync.s95_global_sync_api.fetch_event_activities", side_effect=RuntimeError("boom")):
        result = sync_updated_protocols(db_session, delay_min_sec=0, delay_max_sec=0)

    assert result.errors
    watermark = db_session.query(SyncWatermark).filter(
        SyncWatermark.platform_id == s95_platform.id,
        SyncWatermark.key == S95_PROTOCOLS_RECONCILED_THROUGH,
    ).one_or_none()
    assert watermark is None
