from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Participant,
    Platform,
    PlatformLink,
    PlatformLinkSyncStatus,
    RunResult,
    User,
)
from app.platform_adapters.canonical import CanonicalParticipant, CanonicalRunResult


@pytest.fixture
def s95_link(db_session: Session) -> tuple[PlatformLink, Platform, Participant]:
    external_id = str(uuid4().int % 1_000_000_000)
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add(user)
    db_session.flush()

    platform = db_session.query(Platform).filter(Platform.code == "s95").one()
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_id,
        display_name="Runner",
    )
    db_session.add(participant)
    db_session.flush()

    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        external_user_id=external_id,
        external_url=f"https://s95.ru/athletes/{external_id}/",
        participant_id=participant.id,
        sync_status=PlatformLinkSyncStatus.ok,
    )
    db_session.add(link)
    db_session.commit()
    return link, platform, participant


@patch("app.sync.parkrun_participant_discovery.enqueue_parkrun_discovery_from_barcode")
@patch("app.workers.tasks.s95_sync.fetch_protocol_from_profile_task.apply_async")
@patch("app.sync.s95_protocol_api.fetch_and_upsert_activity_protocol_api")
@patch("app.sync.s95_user_sync.s95_parser.fetch_athlete_activity")
def test_sync_s95_platform_link_enqueues_protocols_without_inline_refetch(
    fetch_activity_mock: MagicMock,
    inline_fetch_mock: MagicMock,
    apply_async_mock: MagicMock,
    _parkrun_mock: MagicMock,
    db_session: Session,
    s95_link: tuple[PlatformLink, Platform, Participant],
) -> None:
    link, platform, participant = s95_link
    slug = f"enqueue-{uuid4().hex[:8]}"
    event_date = date(2099, 7, 1)
    external_id = link.external_user_id

    fetch_activity_mock.return_value = (
        CanonicalParticipant(
            external_user_id=external_id,
            display_name="Runner",
            profile_url=f"https://s95.ru/athletes/{external_id}/",
        ),
        [
            CanonicalRunResult(
                external_result_key=f"profile:{external_id}:{event_date.isoformat()}:{slug}",
                event_date=event_date,
                external_user_id=external_id,
                participant_name="Runner",
                position=11,
                finish_time_sec=1800,
                finish_time_display="30:00",
                location_external_key=slug,
                location_name="Test Park",
            )
        ],
        [],
    )

    db_session.add(
        RunResult(
            participant_id=participant.id,
            event_id=_ensure_event(db_session, platform, slug, event_date).id,
            external_result_key=f"profile:{external_id}:{event_date.isoformat()}:{slug}",
            position=10,
            finish_time_sec=1800,
        )
    )
    db_session.commit()
    _parkrun_mock.return_value = MagicMock(found=False)

    from app.sync.s95_user_sync import sync_s95_platform_link

    result = sync_s95_platform_link(db_session, link, platform)

    inline_fetch_mock.assert_not_called()
    assert result["protocols_refetched"] == 0
    assert apply_async_mock.call_count >= 1


def _ensure_event(db_session: Session, platform: Platform, slug: str, event_date: date):
    from app.models import Event, Location

    location = Location(platform_id=platform.id, external_key=slug, name="Test Park")
    db_session.add(location)
    db_session.flush()
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{slug}:{event_date.isoformat()}",
        event_date=event_date,
        event_number=1,
    )
    db_session.add(event)
    db_session.flush()
    return event
