"""Отзыв на локацию переживает удаление строки результата (аудит, SCHEMA-01).

До миграции 091 оценка и фото висели на run_results.id / volunteer_results.id
с ON DELETE CASCADE, а синк удаляет эти строки штатно: протокол перечитали — у
финишёра другой external_result_key, дедуп профиля оставил более полную копию,
чистка легаси-ключей 5 вёрст. Отзыв уезжал молча, файлы фото оставались в
хранилище сиротами. Теперь ссылка обнуляется (SET NULL), отзыв живёт на своих
location_id / event_date / platform_code, а писатель по возможности перевешивает
его на выжившую строку того же участника.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    LocationRating,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.platform_adapters.canonical import CanonicalRunResult, CanonicalVolunteerResult
from app.services.rating_service import (
    delete_rating,
    list_eligible_runs,
    list_my_ratings,
    upsert_rating,
)
from app.sync import upsert

DAY = date.today()


def _platform(db: Session) -> Platform:
    row = db.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if row is None:
        pytest.skip("five_verst platform not seeded")
    return row


def _seed(db: Session) -> tuple[User, Platform, Location, Event, Participant]:
    platform = _platform(db)
    slug = f"ratings-{uuid4().hex[:8]}"
    user = User(display_name="Оценщик")
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name="Рейтингово",
        source_url=f"https://5verst.ru/{slug}/",
    )
    db.add_all([user, location])
    db.flush()
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{slug}:1:{DAY.isoformat()}",
        event_date=DAY,
        event_number=1,
    )
    participant = Participant(
        platform_id=platform.id,
        external_user_id=str(700000 + uuid4().int % 100000),
        display_name="Оценщик",
    )
    db.add_all([event, participant])
    db.flush()
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=f"https://5verst.ru/userstats/{participant.external_user_id}/",
        )
    )
    db.flush()
    return user, platform, location, event, participant


def _grant_rating_right(
    db: Session, platform: Platform, location: Location, participant: Participant
) -> None:
    """Пять пробежек в истории — порог MIN_RUNS_TO_RATE, без него оценка запрещена."""
    for shift in range(1, 6):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"{location.external_key}:hist:{shift}",
            event_date=DAY.replace(year=DAY.year - 1) + timedelta(days=shift),
            event_number=shift,
        )
        db.add(event)
        db.flush()
        db.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"{location.external_key}:hist:{shift}:{participant.external_user_id}",
                finish_time_sec=1500 + shift,
            )
        )
    db.flush()


def _rating(
    db: Session,
    user: User,
    location: Location,
    *,
    run_result_id=None,
    volunteer_result_id=None,
    participation_type: str = "run",
) -> LocationRating:
    rating = LocationRating(
        user_id=user.id,
        run_result_id=run_result_id,
        volunteer_result_id=volunteer_result_id,
        participation_type=participation_type,
        location_id=location.id,
        location_key=f"location:{location.id}",
        event_date=DAY,
        platform_code="five_verst",
        score_overall=5,
        comment="Отличный старт",
    )
    db.add(rating)
    db.flush()
    return rating


def _canonical_run(slug: str, external_user_id: str, key_suffix: str) -> CanonicalRunResult:
    return CanonicalRunResult(
        external_result_key=f"{slug}:{DAY.isoformat()}:{external_user_id}:{key_suffix}",
        event_date=DAY,
        external_user_id=external_user_id,
        participant_name="Оценщик",
        position=1,
        finish_time_sec=1500,
        finish_time_display="00:25:00",
        age_category="М30-34",
        location_external_key=slug,
        event_number=1,
    )


def test_dedupe_moves_rating_to_the_surviving_row(db_session: Session) -> None:
    """Дедуп профиля удаляет менее полную копию пробежки — отзыв переезжает на неё.

    Раньше отзыв уезжал вместе с копией по CASCADE, хотя сам старт никуда не
    девался: дедуп выбирает, кого оставить, по полноте строки, а не по тому, к
    какой из них привязан отзыв.
    """
    user, platform, location, event, participant = _seed(db_session)
    full = RunResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key=f"{location.external_key}:{DAY.isoformat()}:full",
        position=1,
        age_category="М30-34",
        finish_time_sec=1500,
    )
    poor = RunResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key=f"{location.external_key}:{DAY.isoformat()}:poor",
    )
    db_session.add_all([full, poor])
    db_session.flush()
    rating = _rating(db_session, user, location, run_result_id=poor.id)

    removed = upsert.dedupe_five_verst_run_results_in_db(db_session, platform.id, participant.id)
    db_session.flush()
    db_session.expire_all()

    assert removed == 1
    assert db_session.query(RunResult).filter(RunResult.id == poor.id).one_or_none() is None
    survived = db_session.query(LocationRating).filter(LocationRating.id == rating.id).one_or_none()
    assert survived is not None, "отзыв уехал вместе с дублем строки результата"
    assert survived.run_result_id == full.id


def test_rating_keeps_live_link_when_protocol_rekeys_the_row(db_session: Session) -> None:
    """Перечитка протокола с другим ключом: строка обновляется на месте, отзыв цел."""
    user, platform, location, event, participant = _seed(db_session)
    slug = location.external_key
    upsert.replace_event_run_results(
        db_session, event, platform, [_canonical_run(slug, participant.external_user_id, "a")]
    )
    run = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    rating = _rating(db_session, user, location, run_result_id=run.id)

    upsert.replace_event_run_results(
        db_session, event, platform, [_canonical_run(slug, participant.external_user_id, "b")]
    )
    db_session.flush()
    db_session.expire_all()

    live = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    assert live.external_result_key.endswith(":b")
    survived = db_session.query(LocationRating).filter(LocationRating.id == rating.id).one_or_none()
    assert survived is not None
    assert survived.run_result_id == live.id


def test_rating_survives_when_row_disappears_without_replacement(db_session: Session) -> None:
    """Строку удалили, замены нет: отзыв остаётся с пустой ссылкой, а не исчезает."""
    user, platform, location, event, participant = _seed(db_session)
    slug = location.external_key
    other_id = str(int(participant.external_user_id) + 1)
    upsert.replace_event_run_results(
        db_session,
        platform=platform,
        event=event,
        results=[
            _canonical_run(slug, participant.external_user_id, "a"),
            _canonical_run(slug, other_id, "a"),
        ],
    )
    run = (
        db_session.query(RunResult)
        .filter(RunResult.event_id == event.id, RunResult.participant_id == participant.id)
        .one()
    )
    rating = _rating(db_session, user, location, run_result_id=run.id)

    # Участника вычеркнули из протокола — его строки больше нет.
    upsert.replace_event_run_results(
        db_session, event, platform, [_canonical_run(slug, other_id, "a")]
    )
    db_session.flush()
    db_session.expire_all()

    survived = db_session.query(LocationRating).filter(LocationRating.id == rating.id).one_or_none()
    assert survived is not None
    assert survived.run_result_id is None
    assert survived.comment == "Отличный старт"


def test_volunteer_rating_survives_protocol_rewrite(db_session: Session) -> None:
    user, platform, location, event, participant = _seed(db_session)
    slug = location.external_key

    def _vol(role: str) -> CanonicalVolunteerResult:
        return CanonicalVolunteerResult(
            external_result_key=f"{slug}:{DAY.isoformat()}:vol:{participant.external_user_id}:{role}",
            event_date=DAY,
            external_user_id=participant.external_user_id,
            participant_name="Оценщик",
            role=role,
            source_url="",
            location_external_key=slug,
            event_number=1,
        )

    upsert.replace_event_volunteer_results(db_session, event, platform, [_vol("marshal")])
    vol = db_session.query(VolunteerResult).filter(VolunteerResult.event_id == event.id).one()
    rating = _rating(
        db_session, user, location, volunteer_result_id=vol.id, participation_type="volunteer"
    )

    upsert.replace_event_volunteer_results(db_session, event, platform, [_vol("timekeeper")])
    db_session.flush()
    db_session.expire_all()

    survived = db_session.query(LocationRating).filter(LocationRating.id == rating.id).one_or_none()
    assert survived is not None
    new_vol = db_session.query(VolunteerResult).filter(VolunteerResult.event_id == event.id).one()
    assert survived.volunteer_result_id == new_vol.id


def test_orphan_rating_is_listed_editable_and_deletable(db_session: Session) -> None:
    """Оценка без строки результата видна в «Моих оценках» и правится по 'rate:<id>'."""
    user, platform, location, _event, participant = _seed(db_session)
    _grant_rating_right(db_session, platform, location, participant)
    rating = _rating(db_session, user, location)

    payload = list_my_ratings(db_session, user.id)
    rows = [row for row in payload["ratings"] if row["id"] == rating.id]  # type: ignore[index]
    assert len(rows) == 1, "отзыв без строки результата пропал из «Моих оценок»"
    entry = rows[0]
    assert entry["entry_id"] == f"rate:{rating.id}"
    assert entry["event_date"] == DAY
    assert entry["platform_code"] == "five_verst"
    assert entry["location_name"]

    updated = upsert_rating(
        db_session,
        user,
        f"rate:{rating.id}",
        score_overall=3,
        score_organization=None,
        score_route=None,
        score_community=None,
        comment="Поправил оценку",
        is_public=True,
    )
    assert updated["score_overall"] == 3
    assert updated["entry_id"] == f"rate:{rating.id}"

    assert delete_rating(db_session, user.id, f"rate:{rating.id}") is True
    assert db_session.query(LocationRating).filter(LocationRating.id == rating.id).one_or_none() is None


def test_orphan_rating_is_matched_to_the_same_start_again(db_session: Session) -> None:
    """Строка результата вернулась (пересинк): карточка старта снова показывает отзыв."""
    user, platform, location, event, participant = _seed(db_session)
    slug = location.external_key
    upsert.replace_event_run_results(
        db_session, event, platform, [_canonical_run(slug, participant.external_user_id, "a")]
    )
    run = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    _grant_rating_right(db_session, platform, location, participant)
    rating = _rating(db_session, user, location)  # ссылка потеряна

    entries = list_eligible_runs(db_session, user.id)["runs"]
    mine = [e for e in entries if e["entry_id"] == f"run:{run.id}"]  # type: ignore[index]
    assert len(mine) == 1
    assert mine[0]["my_rating"] is not None
    assert mine[0]["my_rating"]["id"] == rating.id  # type: ignore[index]

    # Правка через обычный entry_id старта возвращает отзыву живую ссылку и не
    # плодит второй отзыв на тот же старт (уникалка по естественному ключу).
    upsert_rating(
        db_session,
        user,
        f"run:{run.id}",
        score_overall=4,
        score_organization=None,
        score_route=None,
        score_community=None,
        comment=None,
        is_public=False,
    )
    db_session.flush()
    db_session.refresh(rating)
    assert rating.run_result_id == run.id
    assert rating.score_overall == 4
    assert db_session.query(LocationRating).filter(LocationRating.user_id == user.id).count() == 1
