"""«Результаты последней субботы»: последний старт каждой идентичности.

Выборка собирается из максимума даты по локации (а не из всех событий каталога,
как раньше — см. QRY-LOCATIONS-02), поэтому тесты держат именно те правила,
которые этим переписаны: дедуп кросслинков, отсечение тестовых событий, склейка
двух систем одной площадки в один день и «последняя суббота».

Режим «За неделю» (/results?week=…) собирается тем же сборщиком строк по
событиям одной недели понедельник–воскресенье, строка — на старт (локация в
конкретный день). Его тесты живут на датах 2031 года: в базе настоящие данные,
а за них ни одно событие не заходит — строки недели целиком наши.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast
from unittest.mock import patch
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
from app.schemas.locations import LastResultsWeekResponse, LastResultsWeeksResponse
from app.services.location_catalog_cache import flush_location_catalog_caches
from app.services.location_page_service import (
    LAST_RESULTS_CACHE_KEY,
    LAST_RESULTS_WEEKS_CACHE_KEY,
    LOCATIONS_INDEX_CACHE_TTL_SECONDS,
    _compute_last_results,
    _compute_last_results_weeks,
    _compute_week_results,
    _histogram_rows,
    build_last_results_weeks,
    build_week_results,
    invalidate_last_results_weeks_cache,
    last_results_week_cache_key,
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


# Неделя A режима «За неделю» — понедельник 2031-01-06 … воскресенье
# 2031-01-12, подписана субботой. Неделя B — предыдущая, со своей субботой.
WEEK_A_MONDAY = date(2031, 1, 6)
WEEK_A_THURSDAY = date(2031, 1, 9)
WEEK_A_SATURDAY = date(2031, 1, 11)
WEEK_A_SUNDAY = date(2031, 1, 12)
WEEK_B_SATURDAY = date(2031, 1, 4)
WEEK_EMPTY_SATURDAY = date(2031, 1, 18)


def _week_world(db_session: Session) -> dict[str, Location]:
    """Неделя A: пять стартов четырёх локаций, плюс то, что стартом не считается.

    - «А» — суббота, два финишёра;
    - «Б» — суббота и четверг-праздник, по два финишёра: два старта одной
      локации — две строки, у каждой своя дата;
    - «В» — одна локация в двух системах: два события в субботу, одна строка
      с суммой финишёров;
    - воскресный RunPark — тоже старт этой недели;
    - тестовый старт и вторичное событие кросслинка в тот же день — не старты;
    - вторичное событие, чьё первичное стоит в ДРУГОЙ неделе (суббота недели
      B), — тоже дубль: дедуп кросслинков общий на всю историю, а не на неделю.
    Неделя B — одна локация «Г» (её событие и есть первичное того кросслинка).
    """
    suffix = uuid4().hex[:8]
    park_a = _location(db_session, "five_verst", f"week-a-{suffix}", "Тестовый парк недели А")
    park_b = _location(db_session, "five_verst", f"week-b-{suffix}", "Тестовый парк недели Б")
    park_c = _location(db_session, "five_verst", f"week-c-{suffix}", "Тестовый парк недели В")
    park_c_mirror = _location(db_session, "s95", f"week-c-{suffix}-s95", "Тестовый парк недели В")
    _link_into_catalog(
        db_session,
        "Тестовый парк недели В",
        [(park_c, "five_verst"), (park_c_mirror, "s95")],
        active="five_verst",
    )
    park_d = _location(db_session, "five_verst", f"week-d-{suffix}", "Тестовый парк недели Г")
    test_only = _location(db_session, "five_verst", f"week-test-{suffix}", "Тестовый парк недели тест")
    bridge = _location(db_session, "runpark", f"week-bridge-{suffix}", "Тестовый мост недели")
    late_bridge = _location(db_session, "runpark", f"week-late-{suffix}", "Тестовый поздний мост недели")
    sunday_park = _location(db_session, "runpark", f"week-sun-{suffix}", "Тестовый воскресный парк недели")

    event_a = _event(db_session, park_a, WEEK_A_SATURDAY, 1)
    _finisher(db_session, event_a, 20 * 60)
    _finisher(db_session, event_a, 21 * 60)
    event_b = _event(db_session, park_b, WEEK_A_SATURDAY, 2)
    _finisher(db_session, event_b, 22 * 60)
    _finisher(db_session, event_b, 23 * 60)
    holiday_event = _event(db_session, park_b, WEEK_A_THURSDAY, 1)
    _finisher(db_session, holiday_event, 26 * 60)
    _finisher(db_session, holiday_event, 27 * 60)
    event_c = _event(db_session, park_c, WEEK_A_SATURDAY, 1)
    _finisher(db_session, event_c, 18 * 60)
    _finisher(db_session, event_c, 19 * 60)
    event_c_mirror = _event(db_session, park_c_mirror, WEEK_A_SATURDAY, 1)
    _finisher(db_session, event_c_mirror, 24 * 60)

    _event(db_session, test_only, WEEK_A_SATURDAY, 1, is_test=True)
    bridge_event = _event(db_session, bridge, WEEK_A_SATURDAY, 1)
    db_session.add(EventCrosslink(primary_event_id=event_a.id, secondary_event_id=bridge_event.id))
    event_d = _event(db_session, park_d, WEEK_B_SATURDAY, 1)
    _finisher(db_session, event_d, 25 * 60)
    late_event = _event(db_session, late_bridge, WEEK_A_SATURDAY, 1)
    db_session.add(EventCrosslink(primary_event_id=event_d.id, secondary_event_id=late_event.id))

    sunday_event = _event(db_session, sunday_park, WEEK_A_SUNDAY, 1)
    _finisher(db_session, sunday_event, 25 * 60)
    db_session.commit()
    return {
        "a": park_a,
        "b": park_b,
        "c": park_c,
        "d": park_d,
        "test_only": test_only,
        "bridge": bridge,
        "late_bridge": late_bridge,
        "sunday": sunday_park,
    }


def test_week_results_row_per_start(db_session: Session) -> None:
    """Неделя A: пять строк — «В» склеена из двух систем, у «Б» два старта, дубли и тест мимо."""
    world = _week_world(db_session)

    payload = _compute_week_results(db_session, WEEK_A_SATURDAY)
    assert payload["saturday"] == WEEK_A_SATURDAY
    assert payload["week_start"] == WEEK_A_MONDAY
    assert payload["week_end"] == WEEK_A_SUNDAY
    items = cast(list[dict[str, Any]], payload["items"])
    # Явка по убыванию; равную явку разводит имя, а два старта «Б» — дата.
    assert [(item["slug"], item["event_date"]) for item in items] == [
        (world["c"].external_key, WEEK_A_SATURDAY),
        (world["a"].external_key, WEEK_A_SATURDAY),
        (world["b"].external_key, WEEK_A_THURSDAY),
        (world["b"].external_key, WEEK_A_SATURDAY),
        (world["sunday"].external_key, WEEK_A_SUNDAY),
    ]
    assert payload["total"] == 5

    merged = items[0]
    assert merged["finishers"] == 3
    assert merged["event_platform_codes"] == ["five_verst", "s95"]
    assert merged["event_platform_code"] == "five_verst"
    assert merged["platform_codes"] == ["five_verst", "s95"]

    # Два старта «Б» — две полноценные строки со своим протоколом.
    thursday, saturday = items[2], items[3]
    assert thursday["identity_key"] == saturday["identity_key"]
    assert (thursday["event_number"], saturday["event_number"]) == (1, 2)
    assert thursday["protocol_url"] != saturday["protocol_url"]

    # Тестовый старт и оба вторичных события кросслинков — не строки недели.
    slugs = {item["slug"] for item in items}
    for excluded in ("test_only", "bridge", "late_bridge", "d"):
        assert world[excluded].external_key not in slugs, excluded
    # «Последней субботы» в этом режиме нет.
    assert all(item["is_last_saturday"] is False for item in items)

    # Ответ ложится в схему эндпоинта как есть.
    LastResultsWeekResponse.model_validate(payload)


def test_week_results_any_day_opens_its_week(db_session: Session) -> None:
    """Любой день недели (старая ссылка ?date=…, ручной ввод) открывает неделю своей субботы."""
    world = _week_world(db_session)

    for day in (WEEK_A_MONDAY, WEEK_A_THURSDAY, WEEK_A_SUNDAY):
        payload = build_week_results(db_session, day, use_cache=False)
        assert payload["saturday"] == WEEK_A_SATURDAY, day
        assert payload["total"] == 5, day

    week_b = build_week_results(db_session, WEEK_B_SATURDAY, use_cache=False)
    assert week_b["week_start"] == date(2030, 12, 30)
    assert [item["slug"] for item in cast(list[dict[str, Any]], week_b["items"])] == [world["d"].external_key]


def test_weeks_list_counts_match_week_rows(db_session: Session) -> None:
    """Локации и старты недели в списке = таблица этой недели; открывается режим на последней неделе."""
    _week_world(db_session)

    payload = _compute_last_results_weeks(db_session)
    weeks = cast(list[dict[str, Any]], payload["weeks"])
    by_saturday = {week["saturday"]: week for week in weeks}
    assert (by_saturday[WEEK_A_SATURDAY]["locations"], by_saturday[WEEK_A_SATURDAY]["starts"]) == (4, 5)
    assert (by_saturday[WEEK_B_SATURDAY]["locations"], by_saturday[WEEK_B_SATURDAY]["starts"]) == (1, 1)
    assert WEEK_EMPTY_SATURDAY not in by_saturday
    for saturday in (WEEK_A_SATURDAY, WEEK_B_SATURDAY):
        rows = cast(list[dict[str, Any]], _compute_week_results(db_session, saturday)["items"])
        assert by_saturday[saturday]["starts"] == len(rows), saturday
        assert by_saturday[saturday]["locations"] == len({row["identity_key"] for row in rows}), saturday

    saturdays = [week["saturday"] for week in weeks]
    assert saturdays == sorted(saturdays)
    assert all(saturday.weekday() == 5 for saturday in saturdays)
    assert payload["latest_saturday"] == WEEK_A_SATURDAY
    LastResultsWeeksResponse.model_validate(payload)


def test_week_cache_key() -> None:
    assert last_results_week_cache_key(WEEK_A_SATURDAY) == "locations:last-results:week:v1:2031-01-11"
    # Ключа «на четверг» не бывает: любой день — ключ своей субботы.
    assert last_results_week_cache_key(WEEK_A_THURSDAY) == last_results_week_cache_key(WEEK_A_SATURDAY)
    assert last_results_week_cache_key(WEEK_A_SUNDAY) == last_results_week_cache_key(WEEK_A_SATURDAY)


def test_week_cache_written_and_flushed(db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    """build_week_results кладёт снимок недели, сброс каталога гасит недели и список недель — и только их."""
    _week_world(db_session)
    payload = build_week_results(db_session, WEEK_A_THURSDAY)
    assert payload["total"] == 5
    week_key = last_results_week_cache_key(WEEK_A_SATURDAY)
    assert 0 < fake_redis.ttl(week_key) <= LOCATIONS_INDEX_CACHE_TTL_SECONDS
    # Второй вызов (другим днём той же недели) читает снимок — дата уже
    # строкой, так она лежит в JSON.
    assert build_week_results(db_session, WEEK_A_SUNDAY)["saturday"] == WEEK_A_SATURDAY.isoformat()

    # Неделя без стартов отвечает пустым и в Redis не пишется: иначе обход
    # случайных дат из адреса копил бы пустые ключи.
    empty = build_week_results(db_session, WEEK_EMPTY_SATURDAY)
    assert empty == {
        "saturday": WEEK_EMPTY_SATURDAY,
        "week_start": date(2031, 1, 13),
        "week_end": date(2031, 1, 19),
        "items": [],
        "total": 0,
    }
    LastResultsWeekResponse.model_validate(empty)
    assert not fake_redis.exists(last_results_week_cache_key(WEEK_EMPTY_SATURDAY))

    build_last_results_weeks(db_session)
    assert 0 < fake_redis.ttl(LAST_RESULTS_WEEKS_CACHE_KEY) <= LOCATIONS_INDEX_CACHE_TTL_SECONDS
    other_week_key = last_results_week_cache_key(WEEK_B_SATURDAY)
    fake_redis.setex(other_week_key, 3600, "{}")
    fake_redis.setex(LAST_RESULTS_CACHE_KEY, 3600, "{}")
    fake_redis.setex("session:keep-me", 3600, "1")

    invalidate_last_results_weeks_cache()

    assert not fake_redis.exists(week_key, other_week_key, LAST_RESULTS_WEEKS_CACHE_KEY)
    # «Последний старт» гасит своя функция, сессии и прочее не трогаем.
    assert fake_redis.exists(LAST_RESULTS_CACHE_KEY)
    assert fake_redis.exists("session:keep-me")

    # Сброс каталога (смена состава локаций) зовёт ту же функцию.
    fake_redis.setex(week_key, 3600, "{}")
    fake_redis.setex(LAST_RESULTS_WEEKS_CACHE_KEY, 3600, "{}")
    with patch("app.workers.tasks.locations_warm.warm_locations_cache.apply_async"):
        flush_location_catalog_caches("тест: состав локаций")
    assert not fake_redis.exists(week_key, LAST_RESULTS_WEEKS_CACHE_KEY)
    assert fake_redis.exists("session:keep-me")


def test_new_protocol_shortens_week_caches(db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    """Перезаписанный воскресный протокол подрезает список недель и снимок своей недели — не чужой."""
    from app.services.location_freshness import (
        STALE_AFTER_WRITE_SECONDS_SHOWCASE,
        mark_location_results_changed,
    )

    suffix = uuid4().hex[:8]
    location = _location(db_session, "runpark", f"week-fresh-{suffix}", "Тестовый парк свежести")
    week_key = last_results_week_cache_key(WEEK_A_SATURDAY)
    other_week_key = last_results_week_cache_key(WEEK_B_SATURDAY)
    for key in (LAST_RESULTS_WEEKS_CACHE_KEY, week_key, other_week_key):
        fake_redis.setex(key, LOCATIONS_INDEX_CACHE_TTL_SECONDS, "{}")

    mark_location_results_changed(
        db_session,
        [location.id],
        reason="тест: протокол недели",
        protocols=[("runpark", WEEK_A_SUNDAY)],
    )
    db_session.commit()

    assert 0 < fake_redis.ttl(LAST_RESULTS_WEEKS_CACHE_KEY) <= STALE_AFTER_WRITE_SECONDS_SHOWCASE
    assert 0 < fake_redis.ttl(week_key) <= STALE_AFTER_WRITE_SECONDS_SHOWCASE
    assert fake_redis.ttl(other_week_key) > STALE_AFTER_WRITE_SECONDS_SHOWCASE


def test_late_weather_shortens_its_week_cache(db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    """Погода приезжает отдельной задачей после протокола и без пар протоколов —
    снимок недели её старта тоже подрезается, иначе «За неделю» показывал бы
    старт без погоды, когда «Последний старт» уже с ней."""
    from app.services.location_freshness import (
        STALE_AFTER_WRITE_SECONDS_SHOWCASE,
        mark_location_results_changed,
    )

    suffix = uuid4().hex[:8]
    location = _location(db_session, "runpark", f"week-weather-{suffix}", "Тестовый парк погоды")
    week_key = last_results_week_cache_key(WEEK_A_SATURDAY)
    other_week_key = last_results_week_cache_key(WEEK_B_SATURDAY)
    for key in (week_key, other_week_key):
        fake_redis.setex(key, LOCATIONS_INDEX_CACHE_TTL_SECONDS, "{}")

    # Четверг-праздник: ключ — суббота его недели, а не сам день.
    mark_location_results_changed(db_session, [location.id], reason="тест: погода", days=[WEEK_A_THURSDAY])
    db_session.commit()

    assert 0 < fake_redis.ttl(week_key) <= STALE_AFTER_WRITE_SECONDS_SHOWCASE
    assert fake_redis.ttl(other_week_key) > STALE_AFTER_WRITE_SECONDS_SHOWCASE


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
