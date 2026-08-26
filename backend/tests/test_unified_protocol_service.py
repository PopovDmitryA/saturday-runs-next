from __future__ import annotations

from datetime import date
from typing import Any
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
    PlatformLink,
    RunResult,
    User,
)
from app.services.unified_protocol_service import (
    build_unified_protocol,
    saturday_of,
    week_start_of,
)

# Неделя далеко в будущем: dev-БД полна настоящих протоколов, и тестовые
# строки не должны смешиваться с ними.
SATURDAY = date(2027, 3, 13)
SUNDAY = date(2027, 3, 14)


def _platform(db: Session, code: str, name: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db.add(platform)
        db.flush()
    return platform


def _location(db: Session, platform: Platform, suffix: str, name: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"uni-{suffix}",
        name=name,
        city="Тюмень",
        country="Россия",
    )
    db.add(location)
    db.flush()
    return location


def _event(db: Session, platform: Platform, location: Location, day: date) -> Event:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"uni-event-{uuid4().hex[:10]}",
        event_date=day,
        event_number=7,
        title=location.name,
    )
    db.add(event)
    db.flush()
    return event


def _runner(
    db: Session,
    platform: Platform,
    suffix: str,
    name: str,
    *,
    gender: str | None = None,
    age_category: str | None = None,
) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"uni-{suffix}",
        display_name=name,
        gender=gender,
        age_category=age_category,
    )
    db.add(participant)
    db.flush()
    return participant


def _result(
    db: Session,
    event: Event,
    participant: Participant | None,
    *,
    position: int | None,
    finish_time_sec: int | None,
    age_category: str | None = None,
) -> None:
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id if participant else None,
            external_result_key=f"{event.external_event_key}:{uuid4().hex[:8]}",
            position=position,
            finish_time_sec=finish_time_sec,
            age_category=age_category,
            status="finished",
        )
    )
    db.flush()


def _build(db: Session, **kwargs: Any) -> dict[str, Any]:
    return build_unified_protocol(db, SATURDAY, use_cache=False, **kwargs)


def test_week_anchor_is_saturday() -> None:
    """Неделя — пн–вс, а подписывается субботой: воскресный RunPark в ней же."""
    assert week_start_of(SATURDAY) == date(2027, 3, 8)
    assert saturday_of(SUNDAY) == SATURDAY
    assert saturday_of(date(2027, 3, 8)) == SATURDAY
    assert saturday_of(date(2027, 3, 12)) == SATURDAY


def test_sunday_runpark_joins_saturday_week(db_session: Session) -> None:
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    runpark = _platform(db_session, "runpark", "RunPark")
    saturday_event = _event(
        db_session, five_verst, _location(db_session, five_verst, "sat", "Затюменский"), SATURDAY
    )
    sunday_event = _event(
        db_session, runpark, _location(db_session, runpark, "sun", "Филейский парк"), SUNDAY
    )

    _result(
        db_session,
        saturday_event,
        _runner(db_session, five_verst, "a", "Анна БЫСТРАЯ", age_category="Ж35-39"),
        position=1,
        finish_time_sec=1100,
        age_category="Ж35-39",
    )
    _result(
        db_session,
        sunday_event,
        _runner(db_session, runpark, "b", "Борис ВОСКРЕСНЫЙ", age_category="VM35-39"),
        position=1,
        finish_time_sec=1000,
        age_category="VM35-39",
    )

    payload = _build(db_session)
    names = [row["name"] for row in payload["results"]]
    # Воскресный старт в том же протоколе и впереди — он быстрее.
    assert names == ["Борис ВОСКРЕСНЫЙ", "Анна БЫСТРАЯ"]
    assert [row["place"] for row in payload["results"]] == [1, 2]
    assert payload["saturday"] == SATURDAY.isoformat()
    assert {item["platform_code"] for item in payload["platforms"]} == {"five_verst", "runpark"}


def test_places_by_gender_and_age_group(db_session: Session) -> None:
    """Три зачёта разом: абсолют, свой пол и своя возрастная группа."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    event = _event(
        db_session, five_verst, _location(db_session, five_verst, "places", "Гагаринский"), SATURDAY
    )

    _result(
        db_session,
        event,
        _runner(db_session, five_verst, "m1", "Первый МУЖ"),
        position=1,
        finish_time_sec=1000,
        age_category="М40-44",
    )
    _result(
        db_session,
        event,
        _runner(db_session, five_verst, "m2", "Второй МУЖ"),
        position=2,
        finish_time_sec=1100,
        age_category="М45-49",
    )
    _result(
        db_session,
        event,
        _runner(db_session, five_verst, "m3", "Третий МУЖ"),
        position=3,
        finish_time_sec=1200,
        age_category="М40-44",
    )
    _result(
        db_session,
        event,
        _runner(db_session, five_verst, "w1", "Первая ЖЕН"),
        position=4,
        finish_time_sec=1300,
        age_category="Ж40-44",
    )

    rows = {row["name"]: row for row in _build(db_session)["results"]}
    assert rows["Третий МУЖ"]["place"] == 3
    assert rows["Третий МУЖ"]["gender_place"] == 3
    assert rows["Третий МУЖ"]["gender_total"] == 3
    # В группе М40-44 он второй, хотя в абсолюте третий.
    assert rows["Третий МУЖ"]["age_group_place"] == 2
    assert rows["Третий МУЖ"]["age_group_total"] == 2
    # Женский зачёт считается отдельно, группа Ж40-44 — тоже.
    assert rows["Первая ЖЕН"]["gender_place"] == 1
    assert rows["Первая ЖЕН"]["age_group_place"] == 1
    assert rows["Первая ЖЕН"]["age_group_total"] == 1


def test_platform_filter_rebuilds_places(db_session: Session) -> None:
    """Система — это зачёт: внутри неё места считаются заново."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    s95 = _platform(db_session, "s95", "S95")
    five_event = _event(
        db_session, five_verst, _location(db_session, five_verst, "scope5", "Затюменский"), SATURDAY
    )
    s95_event = _event(db_session, s95, _location(db_session, s95, "scope95", "Кусково"), SATURDAY)

    _result(
        db_session,
        five_event,
        _runner(db_session, five_verst, "fast", "Быстрый ВЁРСТ"),
        position=1,
        finish_time_sec=900,
        age_category="М30-34",
    )
    _result(
        db_session,
        s95_event,
        _runner(db_session, s95, "slow", "Медленный С95", gender="male"),
        position=1,
        finish_time_sec=1000,
    )

    everywhere = _build(db_session)
    assert [row["place"] for row in everywhere["results"]] == [1, 2]

    scoped = _build(db_session, platform="s95")
    assert scoped["scope_platform"] == "s95"
    assert [(row["name"], row["place"], row["gender_place"]) for row in scoped["results"]] == [
        ("Медленный С95", 1, 1)
    ]
    assert scoped["summary"]["finishers"] == 1


def test_volunteers_are_counted_per_system_not_per_runner(db_session: Session) -> None:
    """Волонтёры: людей и записей — цифры разные, когда ролей больше одной.

    Волонтёрство живёт при старте: ни времени, ни возрастной группы у него нет,
    поэтому цифра берётся по зачёту системы и срезом по полу не сужается.
    """
    payload = _build(db_session)
    assert payload["summary"]["volunteers"] >= payload["summary"]["volunteer_people"]
    women = _build(db_session, gender="female")
    assert women["summary"]["volunteers"] == payload["summary"]["volunteers"]


def test_gender_and_age_group_are_their_own_scopes(db_session: Session) -> None:
    """Пол и группа — тоже зачёты: «№» пересчитывается внутри выбранного.

    Правка Дмитрия 22.08.2026: «при выборе возрастной группы рейтинг тоже
    должен пересчитываться». Места по полу и по группе при этом остаются
    мерой всей недели — по ним видно результат в масштабе системы.
    """
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    event = _event(
        db_session, five_verst, _location(db_session, five_verst, "filters", "Мещерский"), SATURDAY
    )
    _result(
        db_session,
        event,
        _runner(db_session, five_verst, "fm", "Первый МУЖ"),
        position=1,
        finish_time_sec=900,
        age_category="М30-34",
    )
    _result(
        db_session,
        event,
        _runner(db_session, five_verst, "fw1", "Первая ЖЕН"),
        position=2,
        finish_time_sec=1000,
        age_category="Ж30-34",
    )
    _result(
        db_session,
        event,
        _runner(db_session, five_verst, "fw2", "Вторая ЖЕН"),
        position=3,
        finish_time_sec=1100,
        age_category="Ж45-49",
    )

    everyone = _build(db_session)
    assert [row["place"] for row in everyone["results"]] == [1, 2, 3]

    women = _build(db_session, gender="female")
    assert [row["name"] for row in women["results"]] == ["Первая ЖЕН", "Вторая ЖЕН"]
    # Женский зачёт нумеруется с единицы, а не «со второго места в стране».
    assert [row["place"] for row in women["results"]] == [1, 2]
    assert [row["gender_place"] for row in women["results"]] == [1, 2]
    # Плитка «финишёров» по полу НЕ сужается (указание Дмитрия 25.08.2026):
    # она показывает разбивку М/Ж, и схлопывать её в один пол бессмысленно.
    # Знаменатель долей живёт отдельно — в scope_finishers.
    assert women["summary"]["finishers"] == 3
    assert women["summary"]["scope_finishers"] == 2
    assert women["gender_counts"] == {"male": 1, "female": 2, "unknown": 0, "total": 3}

    group = _build(db_session, age_group="30–34")
    assert [row["name"] for row in group["results"]] == ["Первый МУЖ", "Первая ЖЕН"]
    assert [row["place"] for row in group["results"]] == [1, 2]
    # …а место в своей (пол + группа) считается по всей неделе, не по срезу.
    assert [row["age_group_place"] for row in group["results"]] == [1, 1]

    # Разбивка по группам считается до фильтра — иначе из выбранной группы
    # некуда было бы переключиться.
    groups = {item["age_group"]: item for item in group["age_groups"]}
    assert groups["30–34"]["male"] == 1
    assert groups["30–34"]["female"] == 1
    assert "45–49" in groups


def test_crosslinked_event_counted_once(db_session: Session) -> None:
    """Один физический старт в двух системах не удваивает финишёров."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    runpark = _platform(db_session, "runpark", "RunPark")
    primary = _event(
        db_session, five_verst, _location(db_session, five_verst, "xl5", "Ангарские пруды"), SATURDAY
    )
    secondary = _event(
        db_session, runpark, _location(db_session, runpark, "xlrp", "Ангарские пруды"), SATURDAY
    )
    db_session.add(EventCrosslink(primary_event_id=primary.id, secondary_event_id=secondary.id))
    db_session.flush()

    _result(
        db_session,
        primary,
        _runner(db_session, five_verst, "x1", "Один ЧЕЛОВЕК"),
        position=1,
        finish_time_sec=1000,
        age_category="М30-34",
    )
    _result(
        db_session,
        secondary,
        _runner(db_session, runpark, "x2", "Он же ДУБЛЬ"),
        position=1,
        finish_time_sec=1000,
        age_category="VM30-34",
    )

    payload = _build(db_session)
    assert [row["name"] for row in payload["results"]] == ["Один ЧЕЛОВЕК"]
    assert payload["summary"]["finishers"] == 1


def test_foreign_parkrun_is_left_out(db_session: Session) -> None:
    """Зарубежный parkrun вне зачёта: в БД по нему не протокол, а туристы.

    Русский parkrun (связан с каталогом) остаётся — по нему протоколы полные.
    """
    parkrun = _platform(db_session, "parkrun", "parkrun")
    foreign = _location(db_session, parkrun, "southampton-juniors", "Southampton juniors")
    russian = _location(db_session, parkrun, "izmailovo", "Измайлово")

    catalog = LocationCatalog(canonical_name="Измайлово", active_platform="five_verst")
    db_session.add(catalog)
    db_session.flush()
    db_session.add(
        LocationCatalogLink(
            catalog_id=catalog.id,
            platform_id=parkrun.id,
            external_key=russian.external_key,
            location_id=russian.id,
        )
    )
    db_session.flush()

    _result(
        db_session,
        _event(db_session, parkrun, foreign, SATURDAY),
        _runner(db_session, parkrun, "junior", "Junior TOURIST", age_category="JM10-14"),
        position=1,
        finish_time_sec=542,
    )
    _result(
        db_session,
        _event(db_session, parkrun, russian, SATURDAY),
        _runner(db_session, parkrun, "ru", "Русский БЕГУН", age_category="SM30-34"),
        position=1,
        finish_time_sec=1000,
    )

    payload = _build(db_session)
    assert [row["name"] for row in payload["results"]] == ["Русский БЕГУН"]
    assert payload["summary"]["skipped_foreign_parkrun"] == 1


def test_pagination_and_own_rows(db_session: Session) -> None:
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    event = _event(
        db_session, five_verst, _location(db_session, five_verst, "page", "Кировоградские"), SATURDAY
    )
    mine = _runner(db_session, five_verst, "mine", "Дмитрий ПОПОВ")
    for index in range(5):
        _result(
            db_session,
            event,
            mine if index == 3 else _runner(db_session, five_verst, f"p{index}", f"Бегун {index}"),
            position=index + 1,
            finish_time_sec=1000 + index * 10,
            age_category="М30-34",
        )

    user = User(display_name="Дмитрий ПОПОВ", profile_private=False)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            external_user_id=mine.external_user_id,
            external_url=f"https://5verst.ru/userstats/{mine.external_user_id}",
        )
    )
    db_session.flush()

    first = _build(db_session, per_page=2, page=1)
    assert first["pages"] == 3
    assert [row["place"] for row in first["results"]] == [1, 2]
    last = _build(db_session, per_page=2, page=99)
    # Страница за пределами списка не 404-ит, а показывает последнюю.
    assert last["page"] == 3

    with_me = _build(db_session, viewer=user)
    assert [row["name"] for row in with_me["my_results"]] == ["Дмитрий ПОПОВ"]
    assert with_me["my_results"][0]["place"] == 4
    assert sum(1 for row in with_me["results"] if row["is_me"]) == 1


def test_filter_counts_are_faceted(db_session: Session) -> None:
    """Цифры в скобках у фильтра считаются с учётом ОСТАЛЬНЫХ выбранных.

    Правка Дмитрия 25.08.2026: выбрал систему и мужчин — число у возрастной
    группы обязано пересчитаться. Свой фильтр в счёт не идёт, иначе из него
    некуда было бы выйти.
    """
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    s95 = _platform(db_session, "s95", "S95")
    event = _event(
        db_session, five_verst, _location(db_session, five_verst, "facets", "Кузьминки"), SATURDAY
    )
    s95_event = _event(
        db_session, s95, _location(db_session, s95, "facets-s95", "Ангарские"), SATURDAY
    )
    _result(
        db_session,
        event,
        _runner(db_session, five_verst, "cm", "Мужчина 30-34"),
        position=1,
        finish_time_sec=900,
        age_category="М30-34",
    )
    _result(
        db_session,
        event,
        _runner(db_session, five_verst, "cw", "Женщина 30-34"),
        position=2,
        finish_time_sec=1000,
        age_category="Ж30-34",
    )
    _result(
        db_session,
        s95_event,
        _runner(db_session, s95, "cs", "Мужчина S95", gender="male"),
        position=1,
        finish_time_sec=950,
    )

    everyone = _build(db_session)
    men = _build(db_session, gender="male")

    groups_all = {item["age_group"]: item["total"] for item in everyone["age_groups"]}
    groups_men = {item["age_group"]: item["total"] for item in men["age_groups"]}
    # В «30–34» есть и мужчина, и женщина — под мужским фильтром остаётся один.
    assert groups_all["30–34"] == 2
    assert groups_men["30–34"] == 1

    # Сам фильтр пола себя не сужает: обе таблетки остаются кликабельными.
    assert men["gender_counts"] == everyone["gender_counts"]

    # А система — сужается полом.
    platforms_men = {item["platform_code"]: item["finishers"] for item in men["platforms"]}
    platforms_all = {item["platform_code"]: item["finishers"] for item in everyone["platforms"]}
    assert platforms_men["five_verst"] < platforms_all["five_verst"]
