"""«Результаты последней субботы»: последний старт каждой идентичности.

Выборка собирается из максимума даты по локации (а не из всех событий каталога,
как раньше — см. QRY-LOCATIONS-02), поэтому тесты держат именно те правила,
которые этим переписаны: дедуп кросслинков, отсечение тестовых событий, склейка
двух систем одной площадки в один день и «последняя суббота».

Режим «За день» (/results?date=…) собирается тем же сборщиком строк по событиям
одного дня. Его тесты живут на датах 2031 года: в базе настоящие данные, а за
них ни одно событие не заходит — строки дня целиком наши.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import fakeredis
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
from app.schemas.locations import LastResultsDayResponse, LastResultsDaysResponse
from app.services.location_page_service import (
    LAST_RESULTS_CACHE_KEY,
    LAST_RESULTS_DAYS_CACHE_KEY,
    LOCATIONS_INDEX_CACHE_TTL_SECONDS,
    _compute_day_results,
    _compute_last_results,
    _compute_last_results_days,
    _histogram_rows,
    build_day_results,
    invalidate_last_results_days_cache,
    last_results_day_cache_key,
)


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


# Неделя режима «За день»: пятница, суббота, воскресенье и будничный праздник.
DAY_FRIDAY = date(2031, 1, 3)
DAY_SATURDAY = date(2031, 1, 4)
DAY_SUNDAY = date(2031, 1, 5)
DAY_HOLIDAY = date(2031, 1, 9)
DAY_EMPTY = date(2031, 1, 11)


def _day_world(db_session: Session) -> dict[str, Location]:
    """Субботние старты на пять локаций, из которых в таблицу дня идут три.

    - «А» и «Б» — по два финишёра (равная явка: порядок решает имя);
    - «В» — одна локация в двух системах: два события в один день, одна строка;
    - тестовый старт и вторичное событие кросслинка — не старты;
    - вторичное событие, чьё первичное стоит в ДРУГОЙ день (пятница), — тоже
      дубль: дедуп кросслинков общий на всю историю, а не на день.
    Плюс воскресный RunPark (одна локация) и четверговый праздник у «Б».
    """
    suffix = uuid4().hex[:8]
    park_a = _location(db_session, "five_verst", f"day-a-{suffix}", "Тестовый парк дня А")
    park_b = _location(db_session, "five_verst", f"day-b-{suffix}", "Тестовый парк дня Б")
    park_c = _location(db_session, "five_verst", f"day-c-{suffix}", "Тестовый парк дня В")
    park_c_mirror = _location(db_session, "s95", f"day-c-{suffix}-s95", "Тестовый парк дня В")
    _link_into_catalog(
        db_session,
        "Тестовый парк дня В",
        [(park_c, "five_verst"), (park_c_mirror, "s95")],
        active="five_verst",
    )
    test_only = _location(db_session, "five_verst", f"day-test-{suffix}", "Тестовый парк дня тест")
    bridge = _location(db_session, "runpark", f"day-bridge-{suffix}", "Тестовый мост дня")
    late_bridge = _location(db_session, "runpark", f"day-late-{suffix}", "Тестовый поздний мост дня")
    sunday_park = _location(db_session, "runpark", f"day-sun-{suffix}", "Тестовый воскресный парк")

    event_a = _event(db_session, park_a, DAY_SATURDAY, 1)
    _finisher(db_session, event_a, 20 * 60)
    _finisher(db_session, event_a, 21 * 60)
    event_b = _event(db_session, park_b, DAY_SATURDAY, 1)
    _finisher(db_session, event_b, 22 * 60)
    _finisher(db_session, event_b, 23 * 60)
    event_c = _event(db_session, park_c, DAY_SATURDAY, 1)
    _finisher(db_session, event_c, 18 * 60)
    _finisher(db_session, event_c, 19 * 60)
    event_c_mirror = _event(db_session, park_c_mirror, DAY_SATURDAY, 1)
    _finisher(db_session, event_c_mirror, 24 * 60)

    _event(db_session, test_only, DAY_SATURDAY, 1, is_test=True)
    bridge_event = _event(db_session, bridge, DAY_SATURDAY, 1)
    db_session.add(EventCrosslink(primary_event_id=event_a.id, secondary_event_id=bridge_event.id))
    friday_event = _event(db_session, park_a, DAY_FRIDAY, 2)
    late_event = _event(db_session, late_bridge, DAY_SATURDAY, 1)
    db_session.add(EventCrosslink(primary_event_id=friday_event.id, secondary_event_id=late_event.id))

    sunday_event = _event(db_session, sunday_park, DAY_SUNDAY, 1)
    _finisher(db_session, sunday_event, 25 * 60)
    holiday_event = _event(db_session, park_b, DAY_HOLIDAY, 2)
    _finisher(db_session, holiday_event, 26 * 60)
    db_session.commit()
    return {
        "a": park_a,
        "b": park_b,
        "c": park_c,
        "test_only": test_only,
        "bridge": bridge,
        "late_bridge": late_bridge,
        "sunday": sunday_park,
    }


def test_day_results_merge_systems_and_skip_duplicates(db_session: Session) -> None:
    """Суббота: три строки, «В» склеена из двух систем, дубли и тест — мимо."""
    world = _day_world(db_session)

    payload = _compute_day_results(db_session, DAY_SATURDAY)
    items = payload["items"]
    assert payload["date"] == DAY_SATURDAY
    # Явка по убыванию, равную явку «А» и «Б» разводит имя.
    assert [item["slug"] for item in items] == [  # type: ignore[union-attr]
        world["c"].external_key,
        world["a"].external_key,
        world["b"].external_key,
    ]
    assert payload["total"] == 3

    merged = items[0]  # type: ignore[index]
    assert merged["finishers"] == 3
    assert merged["event_platform_codes"] == ["five_verst", "s95"]
    assert merged["event_platform_code"] == "five_verst"
    assert merged["platform_codes"] == ["five_verst", "s95"]
    # Дата у всех строк одна — «последней субботы» в этом режиме нет.
    assert all(item["event_date"] == DAY_SATURDAY for item in items)  # type: ignore[union-attr]
    assert all(item["is_last_saturday"] is False for item in items)  # type: ignore[union-attr]

    # Ответ ложится в схему эндпоинта как есть.
    LastResultsDayResponse.model_validate(payload)


def test_day_results_sunday_and_empty_day(db_session: Session) -> None:
    """Воскресенье — отдельный день со своей единственной локацией; пустой день — пустой ответ."""
    world = _day_world(db_session)

    sunday = _compute_day_results(db_session, DAY_SUNDAY)
    assert [item["slug"] for item in sunday["items"]] == [world["sunday"].external_key]  # type: ignore[union-attr]
    assert sunday["total"] == 1
    assert sunday["items"][0]["event_platform_codes"] == ["runpark"]  # type: ignore[index]

    empty = _compute_day_results(db_session, DAY_EMPTY)
    assert empty == {"date": DAY_EMPTY, "items": [], "total": 0}
    LastResultsDayResponse.model_validate(empty)


def test_days_list_counts_match_day_rows(db_session: Session) -> None:
    """Число локаций дня в списке = строки таблицы этого дня, а открывается режим на субботе.

    Последний день недели — четверговый праздник, после субботы есть и
    воскресенье, но latest_saturday — именно суббота.
    """
    _day_world(db_session)

    payload = _compute_last_results_days(db_session)
    counts = {day["date"]: day["locations"] for day in payload["days"]}  # type: ignore[union-attr,index]
    assert counts[DAY_FRIDAY] == 1
    assert counts[DAY_SATURDAY] == 3
    assert counts[DAY_SUNDAY] == 1
    assert counts[DAY_HOLIDAY] == 1
    assert DAY_EMPTY not in counts
    for day in (DAY_FRIDAY, DAY_SATURDAY, DAY_SUNDAY, DAY_HOLIDAY):
        assert counts[day] == len(_compute_day_results(db_session, day)["items"]), day  # type: ignore[arg-type]

    dates = [day["date"] for day in payload["days"]]  # type: ignore[union-attr,index]
    assert dates == sorted(dates)
    assert dates[-1] == DAY_HOLIDAY
    assert payload["latest_saturday"] == DAY_SATURDAY
    LastResultsDaysResponse.model_validate(payload)


def test_day_cache_key() -> None:
    assert last_results_day_cache_key(DAY_SATURDAY) == "locations:last-results:day:v1:2031-01-04"


def test_day_cache_written_and_flushed(db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    """build_day_results кладёт снимок дня, сброс каталога гасит дни и список дней — и только их."""
    _day_world(db_session)
    payload = build_day_results(db_session, DAY_SUNDAY)
    assert payload["total"] == 1
    day_key = last_results_day_cache_key(DAY_SUNDAY)
    assert 0 < fake_redis.ttl(day_key) <= LOCATIONS_INDEX_CACHE_TTL_SECONDS
    # Второй вызов читает снимок (дата уже строкой — так она лежит в JSON).
    assert build_day_results(db_session, DAY_SUNDAY)["date"] == DAY_SUNDAY.isoformat()

    # День без стартов отвечает пустым и в Redis не пишется: иначе обход
    # случайных дат из адреса копил бы пустые ключи.
    empty = build_day_results(db_session, DAY_EMPTY)
    assert empty["total"] == 0
    assert not fake_redis.exists(last_results_day_cache_key(DAY_EMPTY))

    other_day_key = last_results_day_cache_key(DAY_SATURDAY)
    fake_redis.setex(other_day_key, 3600, "{}")
    fake_redis.setex(LAST_RESULTS_DAYS_CACHE_KEY, 3600, "{}")
    fake_redis.setex(LAST_RESULTS_CACHE_KEY, 3600, "{}")
    fake_redis.setex("session:keep-me", 3600, "1")

    invalidate_last_results_days_cache()

    assert not fake_redis.exists(day_key, other_day_key, LAST_RESULTS_DAYS_CACHE_KEY)
    # «Последний старт» гасит своя функция, сессии и прочее не трогаем.
    assert fake_redis.exists(LAST_RESULTS_CACHE_KEY)
    assert fake_redis.exists("session:keep-me")


def test_new_protocol_shortens_day_caches(db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    """Перезаписанный протокол подрезает и список дней, и снимок своего дня — не чужого."""
    from app.services.location_freshness import (
        STALE_AFTER_WRITE_SECONDS_SHOWCASE,
        mark_location_results_changed,
    )

    suffix = uuid4().hex[:8]
    location = _location(db_session, "runpark", f"day-fresh-{suffix}", "Тестовый парк свежести")
    sunday_key = last_results_day_cache_key(DAY_SUNDAY)
    saturday_key = last_results_day_cache_key(DAY_SATURDAY)
    for key in (LAST_RESULTS_DAYS_CACHE_KEY, sunday_key, saturday_key):
        fake_redis.setex(key, LOCATIONS_INDEX_CACHE_TTL_SECONDS, "{}")

    mark_location_results_changed(
        db_session,
        [location.id],
        reason="тест: протокол дня",
        protocols=[("runpark", DAY_SUNDAY)],
    )
    db_session.commit()

    assert 0 < fake_redis.ttl(LAST_RESULTS_DAYS_CACHE_KEY) <= STALE_AFTER_WRITE_SECONDS_SHOWCASE
    assert 0 < fake_redis.ttl(sunday_key) <= STALE_AFTER_WRITE_SECONDS_SHOWCASE
    assert fake_redis.ttl(saturday_key) > STALE_AFTER_WRITE_SECONDS_SHOWCASE


def test_late_weather_shortens_its_day_cache(db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    """Погода приезжает отдельной задачей после протокола и без пар протоколов —
    снимок её дня тоже подрезается, иначе «За день» показывал бы старт без
    погоды, когда «Последний старт» уже с ней."""
    from app.services.location_freshness import (
        STALE_AFTER_WRITE_SECONDS_SHOWCASE,
        mark_location_results_changed,
    )

    suffix = uuid4().hex[:8]
    location = _location(db_session, "runpark", f"day-weather-{suffix}", "Тестовый парк погоды")
    sunday_key = last_results_day_cache_key(DAY_SUNDAY)
    saturday_key = last_results_day_cache_key(DAY_SATURDAY)
    for key in (sunday_key, saturday_key):
        fake_redis.setex(key, LOCATIONS_INDEX_CACHE_TTL_SECONDS, "{}")

    mark_location_results_changed(db_session, [location.id], reason="тест: погода", days=[DAY_SUNDAY])
    db_session.commit()

    assert 0 < fake_redis.ttl(sunday_key) <= STALE_AFTER_WRITE_SECONDS_SHOWCASE
    assert fake_redis.ttl(saturday_key) > STALE_AFTER_WRITE_SECONDS_SHOWCASE


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
