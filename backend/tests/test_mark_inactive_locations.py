from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import EventSummary, Location, Platform
from app.sync.mark_inactive_locations_paused import mark_inactive_locations_paused


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def test_mark_inactive_locations_paused_skips_cancelled(db_session: Session, s95_platform: Platform) -> None:
    slug = f"inactive-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Inactive Cancelled",
        is_cancelled=True,
        is_paused=False,
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=f"{slug}:old",
            event_date=date.today() - timedelta(weeks=10),
            event_number=1,
            summary_hash="abc",
        )
    )
    db_session.commit()

    mark_inactive_locations_paused(db_session, inactive_weeks=5, platform_codes=("s95",))
    db_session.refresh(location)
    assert location.is_paused is False


def test_mark_inactive_locations_paused_marks_stale_events(db_session: Session, s95_platform: Platform) -> None:
    slug = f"inactive-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Inactive Active",
        is_cancelled=False,
        is_paused=False,
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=f"{slug}:old",
            event_date=date.today() - timedelta(weeks=10),
            event_number=1,
            summary_hash="abc",
        )
    )
    db_session.commit()

    mark_inactive_locations_paused(db_session, inactive_weeks=5, platform_codes=("s95",))
    db_session.refresh(location)
    assert location.is_paused is True
