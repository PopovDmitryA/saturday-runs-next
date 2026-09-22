"""«Результаты последней субботы»: последний старт каждой идентичности.

Выборка собирается из максимума даты по локации (а не из всех событий каталога,
как раньше — см. QRY-LOCATIONS-02), поэтому тесты держат именно те правила,
которые этим переписаны: дедуп кросслинков, отсечение тестовых событий, склейка
двух систем одной площадки в один день и «последняя суббота».
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Event,
    EventCrosslink,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    RunResult,
)
from app.services.location_page_service import _compute_last_results, _histogram_rows


def _platform(db_session: Session, code: str) -> Platform:
    platform = db_session.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=code)
        db_session.add(platform)
        db_session.flush()
    return platform


def _location(db_session: Session, platform_code: str, slug: str, name: str) -> Location:
    platform = _platform(db_session, platform_code)
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name=name,
        city="Тестоград",
        country="Россия",
        is_official_map=platform_code != "parkrun",
    )
    db_session.add(location)
    db_session.flush()
    return location


def _event(
    db_session: Session,
    location: Location,
    event_date: date,
    number: int,
    *,
    is_test: bool = False,
) -> Event:
    event = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"{location.external_key}:{number}:{uuid4().hex[:6]}",
        event_date=event_date,
        event_number=number,
        title=f"{location.name} #{number}",
        is_test_event=is_test,
    )
    db_session.add(event)
    db_session.flush()
    return event


def _finisher(db_session: Session, event: Event, finish_time_sec: int) -> None:
    suffix = uuid4().hex[:10]
    participant = Participant(
        platform_id=event.platform_id,
        external_user_id=f"last-results-{suffix}",
        display_name=f"Бегун {suffix}",
        profile_url=f"https://example.test/{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"last-results-run-{suffix}",
            finish_time_sec=finish_time_sec,
            finish_time_display="00:20:00",
            status="finished",
        )
    )
    db_session.flush()


def _link_into_catalog(
    db_session: Session, name: str, members: list[tuple[Location, str]], *, active: str
) -> None:
    catalog = LocationCatalog(canonical_name=name, active_platform=active)
    db_session.add(catalog)
    db_session.flush()
    for location, platform_code in members:
        db_session.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=_platform(db_session, platform_code).id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )
    db_session.flush()


def test_last_results_merge_systems_and_skip_duplicates(db_session: Session) -> None:
    """Одна площадка в двух системах в одну субботу — одна строка с суммой.

    Заодно проверяются два отсечения: тестовое событие более поздней даты и
    вторичное событие кросслинка (та же пробежка, продублированная другой
    системой) не двигают «последний старт» вперёд.
    """
    suffix = uuid4().hex[:8]
    live = _location(db_session, "five_verst", f"last-res-{suffix}", "Тестовый парк итогов")
    mirror = _location(db_session, "s95", f"last-res-{suffix}-s95", "Тестовый парк итогов")
    _link_into_catalog(
        db_session,
        "Тестовый парк итогов",
        [(live, "five_verst"), (mirror, "s95")],
        active="five_verst",
    )

    _event(db_session, live, date(2026, 2, 28), 1)
    saturday_event = _event(db_session, live, date(2026, 3, 7), 2)
    _finisher(db_session, saturday_event, 20 * 60)
    _finisher(db_session, saturday_event, 22 * 60)
    mirror_event = _event(db_session, mirror, date(2026, 3, 7), 2)
    _finisher(db_session, mirror_event, 19 * 60)

    # Тестовое событие позже — в «последний старт» не идёт.
    _event(db_session, live, date(2026, 3, 21), 3, is_test=True)
    # Вторичное событие кросслинка — тоже: это дубль уже учтённой пробежки.
    # Дата нарочно более поздняя, иначе отсечение не было бы видно.
    duplicate = _event(db_session, mirror, date(2026, 3, 14), 3)
    db_session.add(
        EventCrosslink(
            primary_event_id=saturday_event.id,
            secondary_event_id=duplicate.id,
        )
    )
    db_session.commit()

    payload = _compute_last_results(db_session)
    assert payload["saturday_date"] == date(2026, 3, 7)
    items = [item for item in payload["items"] if item["slug"] == live.external_key]  # type: ignore[index,union-attr]
    assert len(items) == 1
    row = items[0]
    assert row["event_date"] == date(2026, 3, 7)
    assert row["event_platform_codes"] == ["five_verst", "s95"]
    assert row["event_platform_code"] == "five_verst"
    assert row["event_number"] == 2
    assert row["finishers"] == 3
    assert row["is_last_saturday"] is True
    assert row["best_male_time_sec"] is None or row["best_male_time_sec"] == 19 * 60


def test_last_results_skips_location_without_events(db_session: Session) -> None:
    """Локация без единого старта в выдачу не попадает — показывать нечего."""
    suffix = uuid4().hex[:8]
    empty = _location(db_session, "five_verst", f"last-res-empty-{suffix}", "Пустой парк")
    other = _location(db_session, "five_verst", f"last-res-other-{suffix}", "Обычный парк")
    _event(db_session, other, date(2026, 3, 7), 1)
    db_session.commit()

    payload = _compute_last_results(db_session)
    slugs = {item["slug"] for item in payload["items"]}  # type: ignore[index,union-attr]
    assert empty.external_key not in slugs
    assert other.external_key in slugs


def test_histogram_merges_age_groups(db_session: Session) -> None:
    """Гистограмма страницы локации — только корзина и пол.

    QRY-LOCATIONS-03: разбивка по возрастной группе раздувала payload страницы
    в двадцать раз, а фронт её не читает — строки одной корзины и одного пола
    обязаны слиться в одну.
    """
    suffix = uuid4().hex[:8]
    location = _location(db_session, "five_verst", f"hist-{suffix}", "Парк гистограммы")
    event = _event(db_session, location, date(2026, 3, 7), 1)
    for age_category, finish_time_sec in (
        ("М30-34", 20 * 60 + 1),
        ("М55-59", 20 * 60 + 7),
        ("Ж35-39", 20 * 60 + 3),
        ("М30-34", 25 * 60),
    ):
        participant = Participant(
            platform_id=event.platform_id,
            external_user_id=f"hist-{suffix}-{uuid4().hex[:8]}",
            display_name="Бегун",
            profile_url=f"https://example.test/hist-{uuid4().hex[:8]}/",
        )
        db_session.add(participant)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"hist-{uuid4().hex[:10]}",
                finish_time_sec=finish_time_sec,
                finish_time_display="00:20:00",
                age_category=age_category,
                status="finished",
            )
        )
    db_session.commit()

    rows = _histogram_rows(db_session, [event.id])
    # Внутри одной корзины порядок полов не определён — сравниваем как множество.
    assert sorted((row["start_sec"], row["gender"], row["count"]) for row in rows) == [
        (20 * 60, "female", 1),
        (20 * 60, "male", 2),
        (25 * 60, "male", 1),
    ]
    # Корзины по возрасту больше не сериализуются (поле ответа остаётся null).
    assert all("age_group" not in row for row in rows)
    # Порядок строк — по времени, как и раньше.
    assert [row["start_sec"] for row in rows] == sorted(row["start_sec"] for row in rows)
