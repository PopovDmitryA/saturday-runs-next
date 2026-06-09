from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import Participant, Platform
from app.sync.s95_athletes_registry import participant_due_for_profile_recheck, participants_for_profile_recheck
from app.sync.s95_participant_sync import BARCODE_LOOKUP_CHECKED_AT_KEY, PROFILE_CHECKED_AT_KEY


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def _participant(
    db: Session,
    platform: Platform,
    *,
    external_user_id: str,
    profile_checked_at: datetime | None = None,
) -> Participant:
    extra: dict[str, str] = {}
    if profile_checked_at is not None:
        extra[PROFILE_CHECKED_AT_KEY] = profile_checked_at.isoformat()
    row = Participant(
        id=uuid4(),
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name=f"S95 {external_user_id}",
        profile_extra=extra,
    )
    db.add(row)
    db.flush()
    return row


def test_participant_due_for_profile_recheck() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    never = Participant(platform_id=uuid4(), external_user_id="1", profile_extra={})
    stale = Participant(
        platform_id=uuid4(),
        external_user_id="2",
        profile_extra={PROFILE_CHECKED_AT_KEY: (cutoff - timedelta(days=1)).isoformat()},
    )
    fresh = Participant(
        platform_id=uuid4(),
        external_user_id="3",
        profile_extra={PROFILE_CHECKED_AT_KEY: datetime.now(timezone.utc).isoformat()},
    )
    assert participant_due_for_profile_recheck(never, cutoff=cutoff) is True
    assert participant_due_for_profile_recheck(stale, cutoff=cutoff) is True
    assert participant_due_for_profile_recheck(fresh, cutoff=cutoff) is False


def test_participants_for_profile_recheck_skips_unknown_ids(
    db_session: Session,
    s95_platform: Platform,
) -> None:
    suffix = str(uuid4().int % 900000 + 100000)
    unknown = _participant(db_session, s95_platform, external_user_id=f"unknown:loc:{suffix}")

    rows = participants_for_profile_recheck(db_session, s95_platform, limit=5000, recheck_interval_days=30)
    assert unknown not in rows


def test_participants_for_profile_recheck_includes_created_stale_row(
    db_session: Session,
    s95_platform: Platform,
) -> None:
    suffix = str(uuid4().int % 900000 + 100000)
    stale = _participant(
        db_session,
        s95_platform,
        external_user_id=f"8{suffix}",
        profile_checked_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    assert participant_due_for_profile_recheck(stale, cutoff=cutoff)

    checked_at = Participant.profile_extra[PROFILE_CHECKED_AT_KEY].astext
    legacy_checked_at = Participant.profile_extra[BARCODE_LOOKUP_CHECKED_AT_KEY].astext
    cutoff_iso = cutoff.isoformat()
    row = (
        db_session.query(Participant)
        .filter(
            Participant.id == stale.id,
            Participant.platform_id == s95_platform.id,
            Participant.external_user_id.op("~")(r"^\d+$"),
            or_(
                and_(checked_at.is_(None), legacy_checked_at.is_(None)),
                checked_at < cutoff_iso,
                and_(checked_at.is_(None), legacy_checked_at < cutoff_iso),
            ),
        )
        .one_or_none()
    )
    assert row is not None
