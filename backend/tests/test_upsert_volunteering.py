from __future__ import annotations

from collections.abc import Generator
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, VolunteerResult
from app.platform_adapters.canonical import CanonicalVolunteerResult
from app.sync import upsert



@pytest.fixture
def five_verst_platform(db_session: Session) -> Platform:
    return db_session.query(Platform).filter(Platform.code == "five_verst").one()


def _seed_participant(db_session: Session, platform: Platform, external_user_id: str = "790103773") -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Volunteer Tester",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
    )
    db_session.add(participant)
    db_session.flush()
    return participant


def _seed_event(db_session: Session, platform: Platform, location: Location, event_date: date) -> Event:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"vol-test:{event_date.isoformat()}:{location.external_key}",
        event_date=event_date,
        event_number=123,
        title="Volunteer Test Event",
    )
    db_session.add(event)
    db_session.flush()
    return event


def test_prefer_volunteer_role_keeps_specific_role() -> None:
    assert upsert._prefer_volunteer_role("Организатор", "volunteer") == "Организатор"
    assert upsert._prefer_volunteer_role("volunteer", "Организатор") == "Организатор"


def test_is_legacy_five_verst_profile_volunteer_key() -> None:
    user_id = "790103773"
    assert upsert._is_legacy_five_verst_profile_volunteer_key(f"{user_id}:2026-04-04", user_id)
    assert upsert._is_legacy_five_verst_profile_volunteer_key(f"{user_id}:2026-04-04:meshchersky", user_id)
    assert upsert._is_legacy_five_verst_profile_volunteer_key(
        f"{user_id}:meshchersky:386:2026-04-04",
        user_id,
    )
    assert not upsert._is_legacy_five_verst_profile_volunteer_key(
        f"{user_id}:2026-04-04:meshchersky:организатор",
        user_id,
    )
    assert not upsert._is_legacy_five_verst_profile_volunteer_key(
        "meshchersky:2026-04-04:vol:790103773:organizator",
        user_id,
    )


def test_import_profile_volunteer_results_migrates_legacy_keys_and_keeps_all_roles(
    db_session: Session,
    five_verst_platform: Platform,
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    location = Location(
        platform_id=five_verst_platform.id,
        external_key=f"meshchersky-{suffix}",
        name="Мещерский",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = _seed_participant(db_session, five_verst_platform, external_user_id=f"790103773-{suffix}")
    event = _seed_event(db_session, five_verst_platform, location, date(2026, 4, 4))

    db_session.add(
        VolunteerResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"790103773-{suffix}:2026-04-04",
            role="Составление отчёта",
        )
    )
    db_session.commit()

    profile_items = [
        CanonicalVolunteerResult(
            external_result_key=f"790103773-{suffix}:2026-04-04:meshchersky-{suffix}:составление_отчёта",
            event_date=date(2026, 4, 4),
            external_user_id=f"790103773-{suffix}",
            participant_name="Volunteer Tester",
            role="Составление отчёта",
            location_external_key=f"meshchersky-{suffix}",
            location_name="Мещерский",
        ),
        CanonicalVolunteerResult(
            external_result_key=f"790103773-{suffix}:2026-04-04:meshchersky-{suffix}:организатор",
            event_date=date(2026, 4, 4),
            external_user_id=f"790103773-{suffix}",
            participant_name="Volunteer Tester",
            role="Организатор",
            location_external_key=f"meshchersky-{suffix}",
            location_name="Мещерский",
        ),
    ]
    upsert.import_profile_volunteer_results(db_session, five_verst_platform, participant.id, profile_items)
    db_session.commit()

    rows = (
        db_session.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant.id)
        .order_by(VolunteerResult.external_result_key.asc())
        .all()
    )
    assert len(rows) == 2
    assert {row.role for row in rows} == {"Составление отчёта", "Организатор"}
    assert all(
        not upsert._is_legacy_five_verst_profile_volunteer_key(row.external_result_key, participant.external_user_id)
        for row in rows
    )


def test_import_profile_volunteer_results_skips_stale_cleanup_when_empty(
    db_session: Session,
    five_verst_platform: Platform,
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    location = Location(
        platform_id=five_verst_platform.id,
        external_key=f"bitsa-{suffix}",
        name="Битца",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = _seed_participant(db_session, five_verst_platform, external_user_id=f"790103773-{suffix}")
    event = _seed_event(db_session, five_verst_platform, location, date(2026, 5, 2))
    db_session.add(
        VolunteerResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"790103773-{suffix}:2026-05-02:bitsa-{suffix}:организатор",
            role="Организатор",
        )
    )
    db_session.commit()

    upsert.import_profile_volunteer_results(db_session, five_verst_platform, participant.id, [])
    db_session.commit()

    rows = db_session.query(VolunteerResult).filter(VolunteerResult.participant_id == participant.id).all()
    assert len(rows) == 1
    assert rows[0].role == "Организатор"


def test_import_profile_volunteer_results_preserves_protocol_rows(
    db_session: Session,
    five_verst_platform: Platform,
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    location = Location(
        platform_id=five_verst_platform.id,
        external_key=f"meshchersky-{suffix}",
        name="Мещерский",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = _seed_participant(db_session, five_verst_platform, external_user_id=f"790103773-{suffix}")
    event = _seed_event(db_session, five_verst_platform, location, date(2026, 4, 4))

    protocol_row = VolunteerResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key=f"meshchersky-{suffix}:2026-04-04:vol:790103773-{suffix}:organizator",
        role="Организатор",
    )
    db_session.add(protocol_row)
    db_session.commit()

    profile_items = [
        CanonicalVolunteerResult(
            external_result_key=f"790103773-{suffix}:2026-04-04:meshchersky-{suffix}:составление_отчёта",
            event_date=date(2026, 4, 4),
            external_user_id=f"790103773-{suffix}",
            participant_name="Volunteer Tester",
            role="Составление отчёта",
            location_external_key=f"meshchersky-{suffix}",
            location_name="Мещерский",
        )
    ]
    upsert.import_profile_volunteer_results(db_session, five_verst_platform, participant.id, profile_items)
    db_session.commit()

    rows = (
        db_session.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant.id)
        .order_by(VolunteerResult.external_result_key.asc())
        .all()
    )
    assert len(rows) == 2
    assert {row.role for row in rows} == {"Организатор", "Составление отчёта"}


def test_upsert_volunteer_results_does_not_downgrade_role(
    db_session: Session,
    five_verst_platform: Platform,
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    location = Location(
        platform_id=five_verst_platform.id,
        external_key=f"sokolniki-{suffix}",
        name="Сокольники",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    user_id = f"999-{suffix}"
    participant = _seed_participant(db_session, five_verst_platform, external_user_id=user_id)
    event = _seed_event(db_session, five_verst_platform, location, date(2026, 4, 11))

    upsert.upsert_volunteer_results(
        db_session,
        event,
        five_verst_platform,
        [
            CanonicalVolunteerResult(
                external_result_key=f"sokolniki-{suffix}:2026-04-11:vol:{user_id}:organizator",
                event_date=date(2026, 4, 11),
                external_user_id=user_id,
                participant_name="Volunteer Tester",
                role="Организатор",
            )
        ],
    )
    upsert.upsert_volunteer_results(
        db_session,
        event,
        five_verst_platform,
        [
            CanonicalVolunteerResult(
                external_result_key=f"sokolniki-{suffix}:2026-04-11:vol:{user_id}:organizator",
                event_date=date(2026, 4, 11),
                external_user_id=user_id,
                participant_name="Volunteer Tester",
                role="volunteer",
            )
        ],
    )
    db_session.commit()

    row = (
        db_session.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant.id)
        .one()
    )
    assert row.role == "Организатор"
