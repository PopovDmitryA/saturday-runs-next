from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User, VolunteerResult
from app.services.participant_profile_service import (
    resolve_profile_identity,
    try_profile_preview_from_db,
)
from app.services.profile_linking_service import (
    confirm_profile_link_by_participant,
    list_user_profile_links,
    preview_profile_link,
)


def _seed_participant_with_run(db_session: Session, platform_code: str) -> tuple[Platform, Participant, str]:
    platform = db_session.query(Platform).filter(Platform.code == platform_code).one_or_none()
    if platform is None:
        pytest.skip(f"{platform_code} platform not seeded")

    external_user_id = str(uuid4().int % 1_000_000_000)
    if platform_code == "five_verst":
        profile_url = f"https://5verst.ru/userstats/{external_user_id}/"
    elif platform_code == "s95":
        profile_url = f"https://s95.ru/athletes/{external_user_id}/"
    else:
        profile_url = f"https://www.parkrun.org.uk/parkrunner/{external_user_id}/"

    fetched_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Тест УЧАСТНИК",
        profile_url=profile_url,
        fetched_at=fetched_at,
    )
    db_session.add(participant)
    db_session.flush()

    location = Location(
        platform_id=platform.id,
        external_key=f"loc-{external_user_id}",
        name="Тестовая локация",
    )
    db_session.add(location)
    db_session.flush()

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"evt-{external_user_id}",
        event_date=date(2026, 5, 17),
        title="Тест #1",
    )
    db_session.add(event)
    db_session.flush()

    run = RunResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key=f"run-{external_user_id}",
        position=10,
        finish_time_sec=1500,
        finish_time_display="00:25:00",
        fetched_at=fetched_at,
    )
    db_session.add(run)
    db_session.flush()
    return platform, participant, profile_url


def test_try_profile_preview_from_db_returns_freshness(db_session: Session) -> None:
    platform, participant, profile_url = _seed_participant_with_run(db_session, "five_verst")

    preview = try_profile_preview_from_db(db_session, "five_verst", profile_url)
    assert preview is not None
    assert preview.data_source == "database"
    assert preview.external_user_id == participant.external_user_id
    assert preview.display_name == "Тест УЧАСТНИК"
    assert preview.total_runs == 1
    assert preview.data_through_date == date(2026, 5, 17)
    assert preview.data_updated_at == participant.fetched_at
    assert len(preview.recent_activities) == 1


def test_preview_profile_link_uses_db_without_fetch(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _platform, _participant, profile_url = _seed_participant_with_run(db_session, "five_verst")
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add(user)
    db_session.flush()

    def _fail_fetch(*_args, **_kwargs):
        raise AssertionError("live fetch should not be called when DB has profile")

    monkeypatch.setattr(
        "app.platform_adapters.five_verst.adapter.FiveVerstAdapter.fetch_profile_preview",
        _fail_fetch,
    )

    preview = preview_profile_link(db_session, "five_verst", profile_url, user=user)
    assert preview.data_source == "database"
    assert preview.display_name == "Тест УЧАСТНИК"


def _link(db_session: Session, user: User, platform: Platform, participant: Participant | None, **fields) -> PlatformLink:
    external_user_id = participant.external_user_id if participant else str(uuid4().int % 1_000_000_000)
    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        participant_id=participant.id if participant else None,
        external_user_id=external_user_id,
        external_url=f"https://example.test/{external_user_id}/",
        **fields,
    )
    db_session.add(link)
    db_session.flush()
    return link


def _user(db_session: Session) -> User:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add(user)
    db_session.flush()
    return user


LINKED_AT = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def test_profile_links_show_latest_data_update_by_any_path(db_session: Session) -> None:
    """Дата у кнопки «Обновить» — последнее обновление данных любым путём.

    Карточка бэклога 06.10.2026: синк по кнопке был 31.08, а пробежка 03.10
    пришла из протокола по расписанию — карточка показывает 03.10, а не 31.08.
    """
    platform, participant, _profile_url = _seed_participant_with_run(db_session, "five_verst")
    protocol_at = datetime(2026, 10, 3, 8, 55, tzinfo=timezone.utc)
    db_session.query(RunResult).filter(RunResult.participant_id == participant.id).update(
        {RunResult.fetched_at: protocol_at}, synchronize_session=False
    )
    user = _user(db_session)
    manual_sync_at = datetime(2026, 8, 31, 16, 52, tzinfo=timezone.utc)
    link = _link(db_session, user, platform, participant, linked_at=LINKED_AT, last_user_sync_at=manual_sync_at)

    [item] = list_user_profile_links(db_session, user)
    assert item["last_user_sync_at"] == manual_sync_at
    assert item["data_updated_at"] == protocol_at
    assert item["data_through_date"] == date(2026, 5, 17)

    # Волонтёрство из протокола позже пробежки — тоже обновление данных.
    run_event = (
        db_session.query(Event)
        .join(RunResult, RunResult.event_id == Event.id)
        .filter(RunResult.participant_id == participant.id)
        .one()
    )
    vol_event = Event(
        platform_id=platform.id,
        location_id=run_event.location_id,
        external_event_key=f"evt-vol-{participant.external_user_id}",
        event_date=date(2026, 10, 11),
        title="Тест #2",
    )
    db_session.add(vol_event)
    db_session.flush()
    volunteer_at = datetime(2026, 10, 11, 9, 40, tzinfo=timezone.utc)
    db_session.add(
        VolunteerResult(
            event_id=vol_event.id,
            participant_id=participant.id,
            external_result_key=f"vol-{participant.external_user_id}",
            role="Секундомер",
            fetched_at=volunteer_at,
        )
    )
    db_session.flush()
    [item] = list_user_profile_links(db_session, user)
    assert item["data_updated_at"] == volunteer_at
    assert item["data_through_date"] == date(2026, 10, 11)

    # Синк по кнопке, не нашедший нового (RunPark пропускает неизменное), строк
    # не трогает — но «проверили, нового нет» тоже свежесть.
    link.last_user_sync_at = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
    db_session.flush()
    [item] = list_user_profile_links(db_session, user)
    assert item["data_updated_at"] == link.last_user_sync_at


def test_profile_link_from_db_does_not_count_as_data_update(db_session: Session) -> None:
    """Привязка parkrun из базы в источник не ходит — дата остаётся датой данных.

    complete_link_without_sync ставит last_user_sync_at «сейчас»; покажи
    карточка его, профиль, пролежавший в базе с весны, выглядел бы свежим, и
    «Обновить» — единственный путь к новым пробежкам parkrun — не нажали бы.
    """
    _platform, participant, _profile_url = _seed_participant_with_run(db_session, "parkrun")
    user = _user(db_session)

    link = confirm_profile_link_by_participant(db_session, user, participant.id)

    assert link.last_user_sync_at is not None
    [item] = list_user_profile_links(db_session, user)
    assert item["data_updated_at"] == datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    assert item["data_through_date"] == date(2026, 5, 17)


def test_profile_link_without_participant_shows_manual_sync(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if platform is None:
        pytest.skip("parkrun platform not seeded")
    user = _user(db_session)
    synced_at = datetime(2026, 6, 26, 18, 31, tzinfo=timezone.utc)
    _link(db_session, user, platform, None, linked_at=LINKED_AT, last_user_sync_at=synced_at)

    [item] = list_user_profile_links(db_session, user)
    assert item["data_updated_at"] == synced_at
    assert item["data_through_date"] is None


def test_resolve_profile_identity_accepts_five_verst_code() -> None:
    identity = resolve_profile_identity("five_verst", "790103773")
    assert identity.external_user_id == "790103773"
    assert identity.canonical_profile_url.endswith("/790103773/")
