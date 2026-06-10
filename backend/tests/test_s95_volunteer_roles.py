from __future__ import annotations

from datetime import date
from uuid import uuid4

pytest_plugins = ["tests.test_dashboard_api"]

from app.models import Event, Location, Participant, Platform, VolunteerResult
from app.platform_adapters.canonical import CanonicalVolunteerResult
from app.s95.parsers.volunteer_roles import (
    canonical_s95_volunteer_role,
    prefer_s95_volunteer_role,
    s95_volunteer_role_key,
)
from app.sync import upsert


def test_canonical_s95_volunteer_role_maps_serbian_and_latin() -> None:
    assert canonical_s95_volunteer_role("Zagrevanje") == "Проведение разминки"
    assert canonical_s95_volunteer_role("Fotograf") == "Фотограф"
    assert canonical_s95_volunteer_role("Проведение разминки") == "Проведение разминки"
    assert canonical_s95_volunteer_role("Фотограф") == "Фотограф"


def test_s95_volunteer_role_key_collapses_translations() -> None:
    assert s95_volunteer_role_key("Zagrevanje") == s95_volunteer_role_key("Проведение разминки")
    assert s95_volunteer_role_key("Fotograf") == s95_volunteer_role_key("Фотограф")


def test_prefer_s95_volunteer_role_prefers_russian() -> None:
    assert prefer_s95_volunteer_role("Fotograf", "Фотограф") == "Фотограф"
    assert prefer_s95_volunteer_role("Zagrevanje", "Проведение разминки") == "Проведение разминки"


def test_dedupe_participant_volunteer_results_merges_serbian_and_russian(
    db_session,
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one()
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=platform.id,
        external_key=f"novisad-{suffix}",
        name="Novi Sad",
        country="Serbia",
    )
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"vol-role-{suffix}",
        display_name="Role Dedupe Tester",
        profile_url=f"https://s95.rs/athletes/vol-role-{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()

    event_date = date(2026, 4, 25)
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"dedupe-vol:{event_date.isoformat()}:{location.external_key}",
        event_date=event_date,
        title="Novi Sad",
    )
    db_session.add(event)
    db_session.flush()

    db_session.add_all(
        [
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"vol:{location.external_key}:{event_date.isoformat()}:{participant.external_user_id}:zagrevanje",
                role="Zagrevanje",
            ),
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"vol:{location.external_key}:{event_date.isoformat()}:{participant.external_user_id}:provdenie_razminki",
                role="Проведение разминки",
            ),
        ]
    )
    db_session.commit()

    deleted = upsert.dedupe_participant_volunteer_results(db_session, platform.id, participant.id)
    db_session.commit()

    rows = (
        db_session.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant.id)
        .all()
    )
    assert deleted == 1
    assert len(rows) == 1
    assert rows[0].role == "Проведение разминки"
