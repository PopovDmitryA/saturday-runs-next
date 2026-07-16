from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, LocationAnnounceSettings, Participant, Platform, RunResult
from app.services.records_digest_service import build_records_digest


def _get_platform(db_session: Session, code: str, name: str) -> Platform:
    platform = db_session.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db_session.add(platform)
        db_session.flush()
    return platform


def _make_location(db_session: Session, platform: Platform, suffix: str, name: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"digest-loc-{suffix}",
        name=name,
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()
    return location


def _make_event(
    db_session: Session, platform: Platform, location: Location, suffix: str, event_date: date, number: int
) -> Event:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"digest-event-{suffix}-{number}",
        event_date=event_date,
        event_number=number,
    )
    db_session.add(event)
    db_session.flush()
    return event


def _make_participant(db_session: Session, platform: Platform, suffix: str, name: str) -> Participant:
    participant = Participant(
        platform_id=platform.id, external_user_id=f"digest-{suffix}", display_name=name
    )
    db_session.add(participant)
    db_session.flush()
    return participant


def _add_run(
    db_session: Session,
    event: Event,
    participant: Participant,
    *,
    finish_time_sec: int,
    age_category: str,
) -> None:
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"run-{uuid4()}",
            finish_time_sec=finish_time_sec,
            age_category=age_category,
            status="finished",
        )
    )


def test_course_record_ranks_above_age_record(db_session: Session) -> None:
    """Локация с рекордом трассы должна стоять выше локации с одним возрастным рекордом."""
    suffix = str(uuid4().int % 1_000_000)
    platform = _get_platform(db_session, "five_verst", "5 вёрст")

    big = _make_location(db_session, platform, f"{suffix}-big", "Локация с рекордом трассы")
    small = _make_location(db_session, platform, f"{suffix}-small", "Локация с возрастным")

    for location, key in ((big, "big"), (small, "small")):
        past = _make_event(db_session, platform, location, f"{suffix}-{key}", date(2024, 6, 1), 1)
        today = _make_event(db_session, platform, location, f"{suffix}-{key}", date(2024, 6, 8), 2)
        old = _make_participant(db_session, platform, f"{suffix}-{key}-old", "Старый Рекордсмен")
        new = _make_participant(db_session, platform, f"{suffix}-{key}-new", "Новый Герой")
        _add_run(db_session, past, old, finish_time_sec=1000, age_category="М30-34 (1)")
        if key == "big":
            # Быстрее прежнего лучшего — рекорд трассы.
            _add_run(db_session, today, new, finish_time_sec=900, age_category="М30-34 (1)")
        else:
            # Медленнее общего рекорда, но первый в своей группе.
            _add_run(db_session, today, new, finish_time_sec=1500, age_category="Ж50-54 (1)")
    db_session.commit()

    digest = build_records_digest(db_session, date(2024, 6, 8))
    names = [card["location_name"] for card in digest["cards"]]
    assert names.index("Локация с рекордом трассы") < names.index("Локация с возрастным")

    top = digest["cards"][names.index("Локация с рекордом трассы")]
    assert top["importance"] == "high"
    assert len(top["course_records"]) == 1
    assert "Мужской рекорд трассы" in top["post_text"]
    assert "Поздравляю" in top["post_text"]


def test_course_record_holder_not_duplicated_in_age_records(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform = _get_platform(db_session, "five_verst", "5 вёрст")
    location = _make_location(db_session, platform, suffix, "Локация")
    past = _make_event(db_session, platform, location, suffix, date(2024, 6, 1), 1)
    today = _make_event(db_session, platform, location, suffix, date(2024, 6, 8), 2)

    old = _make_participant(db_session, platform, f"{suffix}-old", "Старый Рекордсмен")
    hero = _make_participant(db_session, platform, f"{suffix}-hero", "Единственный Герой")
    _add_run(db_session, past, old, finish_time_sec=1000, age_category="М30-34 (1)")
    _add_run(db_session, today, hero, finish_time_sec=900, age_category="М30-34 (1)")
    db_session.commit()

    digest = build_records_digest(db_session, date(2024, 6, 8))
    card = next(c for c in digest["cards"] if c["location_name"] == "Локация")
    assert [r["record_name"] for r in card["course_records"]] == ["Единственный Герой"]
    # Тот же человек побил и рекорд группы — но второй раз о нём не рассказываем.
    assert card["age_records"] == []


def test_do_not_disturb_locations_sink_to_bottom(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform = _get_platform(db_session, "five_verst", "5 вёрст")
    quiet = _make_location(db_session, platform, f"{suffix}-quiet", "Тихая локация")
    loud = _make_location(db_session, platform, f"{suffix}-loud", "Обычная локация")
    db_session.add(LocationAnnounceSettings(location_id=quiet.id, do_not_disturb=True))

    for location, key in ((quiet, "quiet"), (loud, "loud")):
        past = _make_event(db_session, platform, location, f"{suffix}-{key}", date(2024, 6, 1), 1)
        today = _make_event(db_session, platform, location, f"{suffix}-{key}", date(2024, 6, 8), 2)
        old = _make_participant(db_session, platform, f"{suffix}-{key}-old", "Старый")
        new = _make_participant(db_session, platform, f"{suffix}-{key}-new", "Новый")
        _add_run(db_session, past, old, finish_time_sec=1000, age_category="М30-34 (1)")
        _add_run(db_session, today, new, finish_time_sec=900, age_category="М30-34 (1)")
    db_session.commit()

    digest = build_records_digest(db_session, date(2024, 6, 8))
    names = [c["location_name"] for c in digest["cards"]]
    assert names.index("Обычная локация") < names.index("Тихая локация")
    assert digest["do_not_disturb"] >= 1
