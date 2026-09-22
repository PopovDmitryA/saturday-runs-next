from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from app.models import Event, Location, Participant, VolunteerResult
from app.sync import upsert


def test_dedupe_participant_volunteer_results_keeps_protocol_row(
    db_session,
) -> None:
    platform = upsert.get_platform(db_session, "five_verst")
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=platform.id,
        external_key=f"druzhba-{suffix}",
        name="Дружба",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"user-{suffix}",
        display_name="Volunteer",
    )
    db_session.add(participant)
    db_session.flush()

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"2026-05-30:druzhba-{suffix}",
        event_date=date(2026, 5, 30),
        title="Дружба",
    )
    db_session.add(event)
    db_session.flush()

    db_session.add_all(
        [
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"user-{suffix}:2026-05-30:druzhba-{suffix}:svyazi_s_obschestvennostyu",
                role="Связи с общественностью",
            ),
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"druzhba-{suffix}:2026-05-30:vol:user-{suffix}:svyazi_s_obschestvennostyu",
                role="Связи с общественностью",
            ),
        ]
    )
    db_session.flush()

    deleted = upsert.dedupe_participant_volunteer_results(db_session, platform.id, participant.id)
    rows = (
        db_session.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant.id)
        .all()
    )

    assert deleted == 1
    assert len(rows) == 1
    assert ":vol:" in rows[0].external_result_key


def test_dedupe_collapses_renamed_role_and_keeps_fresh_label(db_session) -> None:
    """Переименование роли внутри системы не должно плодить вторую строку.

    5 вёрст в сентябре 2026 переименовали «Сканирование штрих-кодов» в
    «Сканер» — задним числом, во всех протоколах. Протокол в базе хранил старое
    имя, синк профиля приносил новое, и за одну субботу у человека оказывалось
    два волонтёрства.
    """
    platform = upsert.get_platform(db_session, "five_verst")
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=platform.id,
        external_key=f"meshchersky-{suffix}",
        name="Мещерский",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"user-{suffix}",
        display_name="Volunteer",
    )
    db_session.add(participant)
    db_session.flush()

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"2026-09-19:meshchersky-{suffix}",
        event_date=date(2026, 9, 19),
        title="Мещерский",
    )
    db_session.add(event)
    db_session.flush()

    db_session.add_all(
        [
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=(
                    f"meshchersky-{suffix}:2026-09-19:vol:user-{suffix}:skanirovanie_shtrih_kodov"
                ),
                role="Сканирование штрих-кодов",
                fetched_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
            ),
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"meshchersky-{suffix}:2026-09-19:vol:user-{suffix}:skaner",
                role="Сканер",
                fetched_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
            ),
        ]
    )
    db_session.flush()

    deleted = upsert.dedupe_participant_volunteer_results(db_session, platform.id, participant.id)
    rows = (
        db_session.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant.id)
        .all()
    )

    assert deleted == 1
    assert len(rows) == 1
    # Остаётся то имя, которое у источника видели последним.
    assert rows[0].role == "Сканер"


def test_dedupe_keeps_different_roles_of_one_day(db_session) -> None:
    """Схлопывание — только для одной и той же работы: две разные роли за
    субботу остаются двумя волонтёрствами."""
    platform = upsert.get_platform(db_session, "five_verst")
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=platform.id,
        external_key=f"kuzminki-{suffix}",
        name="Кузьминки",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"user-{suffix}",
        display_name="Volunteer",
    )
    db_session.add(participant)
    db_session.flush()

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"2026-09-19:kuzminki-{suffix}",
        event_date=date(2026, 9, 19),
        title="Кузьминки",
    )
    db_session.add(event)
    db_session.flush()

    db_session.add_all(
        [
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"kuzminki-{suffix}:2026-09-19:vol:user-{suffix}:skaner",
                role="Сканер",
            ),
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"kuzminki-{suffix}:2026-09-19:vol:user-{suffix}:marshal",
                role="Маршал",
            ),
        ]
    )
    db_session.flush()

    deleted = upsert.dedupe_participant_volunteer_results(db_session, platform.id, participant.id)

    assert deleted == 0
    assert (
        db_session.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant.id)
        .count()
        == 2
    )
