"""Колонка «Первый старт в системе» в каталоге локаций.

Сквозная дата отвечает на «когда здесь вообще начали бегать», новая — на
«когда здесь начались 5 вёрст». У парков, переехавших из parkrun-эпохи, это
разные годы, и раньше вторую дату приходилось вычислять руками (просьба
Дмитрия 23.08.2026 — этот вопрос закрывал дашборд «дни рождения» в Grafana).
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, LocationCatalog, LocationCatalogLink, Platform
from app.services.location_page_service import (
    _compute_locations_index,
    _first_event_in_current_system,
    _IndexIdentityStat,
)


def _stat(**first_by_platform: date) -> _IndexIdentityStat:
    stat = _IndexIdentityStat()
    stat.first_event_date_by_platform = dict(first_by_platform)
    return stat


def _member(code: str) -> tuple[Location, str]:
    # Хелперу нужен только код системы — сама Location в выборе не участвует.
    return (SimpleNamespace(), code)  # type: ignore[return-value]


def test_takes_the_first_system_from_the_ordered_list() -> None:
    """`ordered` приходит отсортированным «активная система первой», поэтому
    первый же элемент со стартами и есть актуальная система."""
    code, first = _first_event_in_current_system(
        [_member("five_verst"), _member("parkrun")],
        _stat(five_verst=date(2022, 4, 2), parkrun=date(2012, 9, 1)),
    )
    assert code == "five_verst"
    assert first == date(2022, 4, 2)


def test_skips_a_system_without_starts() -> None:
    """«Актуальная система, по которой есть пробежки»: у площадки может быть
    заведена строка в системе, где стартов ещё не было."""
    code, first = _first_event_in_current_system(
        [_member("s95"), _member("five_verst")],
        _stat(five_verst=date(2021, 5, 15)),
    )
    assert code == "five_verst"
    assert first == date(2021, 5, 15)


def test_no_starts_at_all_gives_nothing() -> None:
    assert _first_event_in_current_system([_member("five_verst")], _stat()) == (None, None)
    assert _first_event_in_current_system([_member("five_verst")], None) == (None, None)


def _platform(db_session: Session, code: str) -> Platform:
    return db_session.query(Platform).filter(Platform.code == code).one()


def _location_with_events(
    db_session: Session,
    *,
    platform_code: str,
    slug: str,
    name: str,
    dates: list[date],
) -> Location:
    platform = _platform(db_session, platform_code)
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name=name,
        city="Тестоград",
        country="Россия",
        latitude=55.75,
        longitude=37.62,
        is_official_map=platform_code != "parkrun",
    )
    db_session.add(location)
    db_session.flush()
    for index, event_date in enumerate(dates):
        db_session.add(
            Event(
                platform_id=platform.id,
                location_id=location.id,
                external_event_key=f"{slug}:{index}",
                event_date=event_date,
                event_number=index + 1,
                title=f"{name} #{index + 1}",
            )
        )
    db_session.flush()
    return location


def _link_into_catalog(
    db_session: Session, name: str, members: list[tuple[Location, str]], *, active: str
) -> LocationCatalog:
    """Связать строки разных систем в одну каноническую площадку.

    Без этой связки строки остаются РАЗНЫМИ идентичностями (`location:<id>`
    вместо общего `catalog:<id>`), и сквозная история не склеивается — то же
    самое случилось бы и с настоящим парком, забытым в каталоге.
    """
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
    return catalog


def test_migrated_location_shows_both_dates(db_session: Session) -> None:
    """Площадка с прошлым в parkrun: сквозная дата — из parkrun-эпохи, дата в
    системе — с момента, когда её подхватили 5 вёрст."""
    suffix = uuid4().hex[:8]
    slug = f"first-in-system-{suffix}"
    legacy = _location_with_events(
        db_session,
        platform_code="parkrun",
        slug=f"{slug}-parkrun",
        name="Тестовый парк перехода",
        dates=[date(2015, 6, 6), date(2015, 6, 13)],
    )
    live = _location_with_events(
        db_session,
        platform_code="five_verst",
        slug=slug,
        name="Тестовый парк перехода",
        dates=[date(2022, 4, 2), date(2022, 4, 9)],
    )
    _link_into_catalog(
        db_session,
        "Тестовый парк перехода",
        [(legacy, "parkrun"), (live, "five_verst")],
        active="five_verst",
    )
    db_session.commit()

    items = _compute_locations_index(db_session)["items"]
    row = next(item for item in items if item["slug"] == slug)  # type: ignore[index]

    assert row["first_event_date"] == date(2015, 6, 6)
    assert row["first_event_date_in_system"] == date(2022, 4, 2)
    assert row["first_event_system_code"] == "five_verst"


def test_single_system_location_has_matching_dates(db_session: Session) -> None:
    """Без прошлого в других системах обе даты совпадают — и это не дубль:
    колонки расходятся ровно там, где было интересно."""
    slug = f"first-in-system-solo-{uuid4().hex[:8]}"
    _location_with_events(
        db_session,
        platform_code="five_verst",
        slug=slug,
        name="Тестовый парк без прошлого",
        dates=[date(2023, 3, 4), date(2023, 3, 11)],
    )
    db_session.commit()

    items = _compute_locations_index(db_session)["items"]
    row = next(item for item in items if item["slug"] == slug)  # type: ignore[index]

    assert row["first_event_date"] == date(2023, 3, 4)
    assert row["first_event_date_in_system"] == date(2023, 3, 4)
    assert row["first_event_system_code"] == "five_verst"
