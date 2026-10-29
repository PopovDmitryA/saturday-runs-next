"""Календарь стартов локации для планировщика: строки идентичности, что
планируется, субботы и особые даты, ≈№, время старта и отмена."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    LocationDescription,
    Platform,
    StartSpecialDate,
)
from app.services.start_calendar_service import (
    REASON_PARKRUN,
    REASON_PAUSED,
    REASON_SERIES,
    REASON_SUNDAY,
    REASON_UPCOMING,
    cancelled_start_date,
    day_label,
    identity_rows,
    normalize_identity_key,
    plannable_rows,
    plannable_rows_with_reason,
    start_dates,
)

# Среда; ближайшая суббота — 24.10, прошлая — 17.10.
TODAY = date(2026, 10, 21)
SAT1 = date(2026, 10, 24)
SAT2 = date(2026, 10, 31)
PAST_SAT = date(2026, 10, 17)


def _platform(db: Session, code: str) -> Platform:
    return db.query(Platform).filter(Platform.code == code).one()


def _location(db: Session, code: str, **fields: object) -> Location:
    location = Location(
        platform_id=_platform(db, code).id,
        external_key=f"cal-{code}-{uuid4().hex[:8]}",
        name=fields.pop("name", f"Локация {code}"),
        city="Москва",
        country="Россия",
        **fields,
    )
    db.add(location)
    db.flush()
    return location


def _event(db: Session, location: Location, on_date: date, number: int | None, *, is_test: bool = False) -> Event:
    event = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"cal-{location.external_key}-{on_date.isoformat()}",
        event_date=on_date,
        event_number=number,
        is_test_event=is_test,
    )
    db.add(event)
    db.flush()
    return event


def _special(db: Session, on_date: date, platforms: list[str], title: str = "Праздничный старт") -> StartSpecialDate:
    """Особая дата; 01.01.2027 уже есть в сиде миграции — тогда правим его."""
    item = db.query(StartSpecialDate).filter(StartSpecialDate.start_date == on_date).one_or_none()
    if item is None:
        item = StartSpecialDate(start_date=on_date, title=title, platforms=platforms)
        db.add(item)
    else:
        item.title, item.platforms = title, platforms
    db.flush()
    return item


def _rows(db: Session, location: Location) -> list[tuple[Location, str]]:
    return identity_rows(db, f"location:{location.id}")


# ---------------------------------------------------------------- строки идентичности


def test_identity_rows_for_location_and_catalog_keys(db_session: Session) -> None:
    five = _location(db_session, "five_verst")
    s95 = _location(db_session, "s95")
    unlinked_runpark = _location(db_session, "runpark")
    catalog = LocationCatalog(canonical_name="Узел", active_platform="s95")
    db_session.add(catalog)
    db_session.flush()
    db_session.add_all(
        [
            LocationCatalogLink(
                catalog_id=catalog.id, platform_id=five.platform_id, external_key=five.external_key, location_id=five.id
            ),
            LocationCatalogLink(
                catalog_id=catalog.id, platform_id=s95.platform_id, external_key=s95.external_key, location_id=s95.id
            ),
            # Связка без location_id — локация находится по (система, слаг).
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=unlinked_runpark.platform_id,
                external_key=unlinked_runpark.external_key,
            ),
        ]
    )
    db_session.flush()

    assert [loc.id for loc, _code in _rows(db_session, five)] == [five.id]
    rows = identity_rows(db_session, f"catalog:{catalog.id}")
    # Действующая система узла — первой, дальше порядок систем сайта.
    assert [code for _loc, code in rows] == ["s95", "five_verst", "runpark"]
    assert identity_rows(db_session, "catalog:not-a-uuid") == []
    assert identity_rows(db_session, "garbage") == []
    assert identity_rows(db_session, f"catalog:{uuid4()}") == []


# ---------------------------------------------------------------- что планируется


def test_parkrun_series_paused_upcoming_are_not_plannable(db_session: Session) -> None:
    cases = [
        (_location(db_session, "parkrun"), REASON_PARKRUN),
        (_location(db_session, "five_verst", is_series=True), REASON_SERIES),
        (_location(db_session, "five_verst", is_upcoming=True), REASON_UPCOMING),
        (_location(db_session, "s95", is_paused=True), REASON_PAUSED),
    ]
    for location, reason in cases:
        rows, why = plannable_rows_with_reason(db_session, _rows(db_session, location), today=TODAY)
        assert rows == [] and why == reason
    active = _location(db_session, "s95")
    _event(db_session, active, PAST_SAT, 40)
    assert plannable_rows(db_session, _rows(db_session, active), today=TODAY) == _rows(db_session, active)
    assert plannable_rows_with_reason(db_session, [])[1] is not None


def test_sunday_runpark_is_not_plannable(db_session: Session) -> None:
    sunday = _location(db_session, "runpark")
    saturday = _location(db_session, "runpark")
    for week in range(12):
        _event(db_session, sunday, PAST_SAT + timedelta(days=1) - timedelta(weeks=week), week + 1)
        _event(db_session, saturday, PAST_SAT - timedelta(weeks=week), week + 1)
    rows, reason = plannable_rows_with_reason(db_session, _rows(db_session, sunday), today=TODAY)
    assert rows == [] and reason == REASON_SUNDAY
    assert plannable_rows(db_session, _rows(db_session, saturday), today=TODAY) != []


def _catalog(db: Session, *locations: Location, is_closed: bool = False) -> str:
    catalog = LocationCatalog(canonical_name="Узел", active_platform="five_verst", is_closed=is_closed)
    db.add(catalog)
    db.flush()
    for location in locations:
        db.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=location.platform_id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )
    db.flush()
    return f"catalog:{catalog.id}"


def test_old_runpark_row_without_coords_does_not_keep_location_alive(db_session: Session) -> None:
    """Чертаново: 5 вёрст на паузе, а у RunPark-строки без координат флага паузы
    нет никогда (ночное правило молчания такие строки пропускает), последний
    старт — 2022 год. Сайт пишет «Не действует» — планировщик тоже (CAL-1)."""
    paused = _location(db_session, "five_verst", is_paused=True)
    _event(db_session, paused, date(2024, 4, 27), 150)
    dead_runpark = _location(db_session, "runpark", latitude=None, longitude=None)
    _event(db_session, dead_runpark, date(2022, 4, 16), 80)
    key = _catalog(db_session, paused, dead_runpark)

    rows, reason = plannable_rows_with_reason(db_session, identity_rows(db_session, key), today=TODAY)
    assert rows == [] and reason == REASON_PAUSED


def test_dead_row_does_not_bring_its_system_special_dates(db_session: Session) -> None:
    """Живая 5 вёрст и мёртвая RunPark-строка: особая дата только для RunPark
    в календарь живой локации не встаёт."""
    alive = _location(db_session, "five_verst")
    _event(db_session, alive, PAST_SAT, 300)
    dead_runpark = _location(db_session, "runpark", latitude=None, longitude=None)
    _event(db_session, dead_runpark, date(2022, 4, 16), 80)
    key = _catalog(db_session, alive, dead_runpark)
    holiday = date(2026, 11, 4)
    _special(db_session, holiday, ["runpark"])

    plannable = plannable_rows(db_session, identity_rows(db_session, key), today=TODAY)
    assert [location.id for location, _code in plannable] == [alive.id]
    dates = start_dates(db_session, plannable, date_from=TODAY, date_to=date(2026, 11, 8), today=TODAY)
    assert holiday not in {item.date for item in dates}


def test_site_activity_rules_for_planning(db_session: Session) -> None:
    """Стартов не было — «ещё не открылась»; молчание дольше ста дней — пауза;
    ручное «Действует» сильнее дат; закрытый узел каталога — пауза; отмена
    паузой не считается."""
    never = _location(db_session, "s95")
    rows, reason = plannable_rows_with_reason(db_session, _rows(db_session, never), today=TODAY)
    assert rows == [] and reason == REASON_UPCOMING

    silent = _location(db_session, "s95")
    _event(db_session, silent, TODAY - timedelta(days=101), 10)
    rows, reason = plannable_rows_with_reason(db_session, _rows(db_session, silent), today=TODAY)
    assert rows == [] and reason == REASON_PAUSED
    # Ровно сто дней — ещё жива.
    edge = _location(db_session, "s95")
    _event(db_session, edge, TODAY - timedelta(days=100), 10)
    assert plannable_rows(db_session, _rows(db_session, edge), today=TODAY) != []
    # Тестовый старт — не признак жизни.
    test_only = _location(db_session, "s95")
    _event(db_session, test_only, PAST_SAT, 0, is_test=True)
    assert plannable_rows_with_reason(db_session, _rows(db_session, test_only), today=TODAY)[1] == REASON_UPCOMING

    manual = _location(db_session, "s95", status_override="active")
    _event(db_session, manual, TODAY - timedelta(days=300), 10)
    assert plannable_rows(db_session, _rows(db_session, manual), today=TODAY) != []
    manual_new = _location(db_session, "s95", status_override="active")
    assert plannable_rows(db_session, _rows(db_session, manual_new), today=TODAY) != []

    in_closed = _location(db_session, "five_verst")
    _event(db_session, in_closed, PAST_SAT, 50)
    closed_key = _catalog(db_session, in_closed, is_closed=True)
    rows, reason = plannable_rows_with_reason(db_session, identity_rows(db_session, closed_key), today=TODAY)
    assert rows == [] and reason == REASON_PAUSED

    # Отменили ближайшую субботу, а молчание уже длинное и ночное правило
    # поставило паузу: сайт показывает «Отмена старта», а не «Не действует».
    cancelled = _location(db_session, "s95", is_cancelled=True, is_paused=True)
    _event(db_session, cancelled, TODAY - timedelta(days=120), 10)
    assert plannable_rows(db_session, _rows(db_session, cancelled), today=TODAY) != []


def test_special_dates_do_not_blur_runpark_weekday(db_session: Session) -> None:
    """Праздничные будние старты не превращают субботний RunPark в «не субботний»."""
    location = _location(db_session, "runpark")
    # Шесть праздничных сред среди десяти последних стартов — без исключения
    # особых дат мода была бы средой.
    for offset in range(6):
        holiday = PAST_SAT - timedelta(days=3) - timedelta(weeks=offset)
        _special(db_session, holiday, ["runpark"])
        _event(db_session, location, holiday, offset + 100)
    for week in range(4):
        _event(db_session, location, PAST_SAT - timedelta(weeks=week), week + 1)
    assert plannable_rows(db_session, _rows(db_session, location)) != []


# ---------------------------------------------------------------- даты и подписи


def test_start_dates_are_saturdays_and_matching_special_dates(db_session: Session) -> None:
    location = _location(db_session, "s95")
    _special(db_session, date(2026, 11, 4), ["s95"], title="День народного единства")
    _special(db_session, date(2026, 11, 5), ["five_verst"])
    dates = start_dates(db_session, _rows(db_session, location), date_from=TODAY, date_to=date(2026, 11, 8), today=TODAY)
    assert [item.date for item in dates] == [SAT1, SAT2, date(2026, 11, 4), date(2026, 11, 7)]
    special = dates[2]
    assert special.kind == "special" and special.title == "День народного единства"
    # Время особого старта — «уточняйте»; у S95 суббота всегда в 9:00.
    assert special.start_time is None
    assert dates[0].kind == "regular" and dates[0].start_time == "09:00"
    assert all(item.platform_code == "s95" and item.location_id == location.id for item in dates)


def test_start_dates_survive_the_end_of_the_date_range(db_session: Session) -> None:
    """Окно у края date.max: шаг по субботам не переполняется, а дат за
    последней, которую календарь обходит, просто нет (ревью 28.10.2026,
    CORE-3/FUZZ-2: GET /planner/my с 9999-12-31 отвечал 500)."""
    rows = _rows(db_session, _location(db_session, "s95"))
    tail = start_dates(db_session, rows, date_from=date(9999, 12, 1), date_to=date.max, today=TODAY)
    assert [item.date for item in tail] == [date(9999, 12, 4), date(9999, 12, 11), date(9999, 12, 18)]
    assert start_dates(db_session, rows, date_from=date(9999, 12, 26), date_to=date.max, today=TODAY) == []


def test_five_verst_start_time_comes_from_schedule(db_session: Session) -> None:
    location = _location(db_session, "five_verst")
    db_session.add(
        LocationDescription(
            location_id=location.id,
            schedule_parsed=[{"from_month": 10, "to_month": 3, "time": "10:00"}, {"from_month": 4, "to_month": 9, "time": "09:00"}],
        )
    )
    db_session.flush()
    dates = start_dates(db_session, _rows(db_session, location), date_from=TODAY, date_to=SAT1, today=TODAY)
    assert [(item.date, item.start_time) for item in dates] == [(SAT1, "10:00")]


def test_special_saturday_of_other_system_stays_regular(db_session: Session) -> None:
    location = _location(db_session, "five_verst")
    _special(db_session, SAT2, ["s95"])
    dates = start_dates(db_session, _rows(db_session, location), date_from=SAT2, date_to=SAT2, today=TODAY)
    assert [(item.date, item.kind, item.title) for item in dates] == [(SAT2, "regular", None)]


def test_number_forecast_counts_saturdays_and_confirmed_special_dates(db_session: Session) -> None:
    confirmed = _location(db_session, "five_verst")
    unconfirmed = _location(db_session, "five_verst")
    holiday = date(2026, 11, 4)
    _special(db_session, holiday, ["five_verst"])
    _event(db_session, confirmed, PAST_SAT, 100)
    _event(db_session, unconfirmed, PAST_SAT, 100)
    # Год назад в этот праздник у первой локации старт был.
    _event(db_session, confirmed, date(2025, 11, 4), 50)

    def numbers(location: Location) -> dict[date, tuple[int | None, bool | None]]:
        items = start_dates(db_session, _rows(db_session, location), date_from=TODAY, date_to=date(2026, 11, 7), today=TODAY)
        return {item.date: (item.number_forecast, item.last_year) for item in items}

    assert numbers(confirmed) == {
        SAT1: (101, None),
        SAT2: (102, None),
        holiday: (103, True),
        date(2026, 11, 7): (104, None),
    }
    # Праздник без старта год назад сам получает «следующий» номер, но
    # субботы после него не сдвигает: старт не подтверждён.
    assert numbers(unconfirmed) == {
        SAT1: (101, None),
        SAT2: (102, None),
        holiday: (103, False),
        date(2026, 11, 7): (103, None),
    }


def test_number_forecast_ignores_tests_and_stale_anchor(db_session: Session) -> None:
    fresh = _location(db_session, "s95")
    _event(db_session, fresh, PAST_SAT, 40)
    _event(db_session, fresh, PAST_SAT + timedelta(days=1), 0, is_test=True)
    stale = _location(db_session, "s95")
    _event(db_session, stale, PAST_SAT - timedelta(weeks=10), 40)

    fresh_dates = start_dates(db_session, _rows(db_session, fresh), date_from=SAT1, date_to=SAT1, today=TODAY)
    assert fresh_dates[0].number_forecast == 41
    stale_dates = start_dates(db_session, _rows(db_session, stale), date_from=SAT1, date_to=SAT1, today=TODAY)
    assert stale_dates[0].number_forecast is None


def test_cancellation_marks_only_the_nearest_start(db_session: Session) -> None:
    location = _location(db_session, "s95", is_cancelled=True, cancel_reason="Каток в парке")
    dates = start_dates(db_session, _rows(db_session, location), date_from=TODAY, date_to=SAT2, today=TODAY)
    assert [(item.date, item.cancelled, item.cancel_reason) for item in dates] == [
        (SAT1, True, "Каток в парке"),
        (SAT2, False, None),
    ]


def test_cancelled_start_has_no_number_and_does_not_shift_next_ones(db_session: Session) -> None:
    """Отменённый старт номер не расходует: ≈№ на нём нет, следующая суббота
    получает тот номер, что достался бы ему (BE-1)."""
    location = _location(db_session, "s95", is_cancelled=True)
    _event(db_session, location, PAST_SAT, 213)
    by = {
        item.date: item
        for item in start_dates(
            db_session, _rows(db_session, location), date_from=TODAY, date_to=date(2026, 11, 7), today=TODAY
        )
    }
    assert by[SAT1].cancelled is True and by[SAT1].number_forecast is None
    assert by[SAT2].number_forecast == 214
    assert by[date(2026, 11, 7)].number_forecast == 215


def test_cancelled_start_date_is_the_saturday_unless_special_date_is_confirmed(db_session: Session) -> None:
    """Флаг отмены — о ближайшей субботе. Особая дата до неё забирает его,
    только если она впереди и год назад в неё бежали (NT-1)."""
    location = _location(db_session, "five_verst")
    ids = [location.id]
    assert cancelled_start_date(db_session, ids, "five_verst", TODAY) == SAT1
    assert cancelled_start_date(db_session, ids, "five_verst", SAT1) == SAT1
    holiday = date(2026, 10, 22)
    _special(db_session, holiday, ["five_verst"])
    # Год назад в эту дату старта не было — отмена о субботе.
    assert cancelled_start_date(db_session, ids, "five_verst", TODAY) == SAT1
    _event(db_session, location, date(2025, 10, 22), 100)
    assert cancelled_start_date(db_session, ids, "five_verst", TODAY) == holiday
    # Другой системе эта особая дата не своя.
    assert cancelled_start_date(db_session, ids, "s95", TODAY) == SAT1
    # В день особой даты флаг — уже о субботе.
    assert cancelled_start_date(db_session, ids, "five_verst", holiday) == SAT1


def test_cancelled_mark_does_not_depend_on_calendar_window(db_session: Session) -> None:
    """Пятница 01.01 — особая дата, у локации её старта год назад не было, а
    отменили субботу 02.01. Метка «отменён» — у 02.01 при любом окне
    календаря и с опорным стартом или без (NT-2)."""
    today = date(2026, 12, 28)
    new_year, saturday = date(2027, 1, 1), date(2027, 1, 2)
    _special(db_session, new_year, ["five_verst"], title="Новогодний старт")
    with_anchor = _location(db_session, "five_verst", is_cancelled=True)
    _event(db_session, with_anchor, date(2026, 12, 26), 100)
    without_anchor = _location(db_session, "five_verst", is_cancelled=True)

    for location in (with_anchor, without_anchor):
        for span in ((saturday, saturday), (new_year, saturday), (today, date(2027, 1, 9))):
            marks = {
                item.date: item.cancelled
                for item in start_dates(db_session, _rows(db_session, location), date_from=span[0], date_to=span[1], today=today)
            }
            assert marks[saturday] is True, (location.external_key, span)
            assert marks.get(new_year) in (None, False), (location.external_key, span)


def test_last_year_looks_at_all_identity_rows(db_session: Session) -> None:
    """Раменское: 01.01 прошлого года бежали в 5 вёрст, теперь 5 вёрст на паузе и
    планируется только S95 — «в прошлом году старт был» всё равно да, и слот
    01.01 сдвигает номер следующей субботы (CAL-4)."""
    old_row = _location(db_session, "five_verst", is_paused=True)
    _event(db_session, old_row, date(2026, 1, 1), 194)
    alive = _location(db_session, "s95")
    _event(db_session, alive, date(2026, 12, 26), 15)
    key = _catalog(db_session, old_row, alive)
    _special(db_session, date(2027, 1, 1), ["five_verst", "s95"], title="Новогодний старт")
    today = date(2026, 12, 28)
    rows = identity_rows(db_session, key)
    plannable = plannable_rows(db_session, rows, today=today)
    assert [location.id for location, _code in plannable] == [alive.id]

    span = {"date_from": date(2027, 1, 1), "date_to": date(2027, 1, 2), "today": today}
    only_plannable = {item.date: item for item in start_dates(db_session, plannable, **span)}
    assert only_plannable[date(2027, 1, 1)].last_year is False
    by = {item.date: item for item in start_dates(db_session, plannable, history_rows=rows, **span)}
    assert by[date(2027, 1, 1)].last_year is True
    assert by[date(2027, 1, 1)].number_forecast == 16
    assert by[date(2027, 1, 2)].number_forecast == 17


def test_normalize_identity_key() -> None:
    raw = uuid4()
    for spelling in (str(raw).upper(), raw.hex, f"{{{raw}}}", f"urn:uuid:{raw}", f"  {raw}  "):
        assert normalize_identity_key(f"catalog:{spelling.strip()}") == f"catalog:{raw}"
    assert normalize_identity_key(f"location:{raw.hex.upper()}") == f"location:{raw}"
    assert normalize_identity_key("catalog:not-a-uuid") is None
    assert normalize_identity_key(f"other:{raw}") is None
    assert normalize_identity_key(None) is None


def test_seed_new_year_is_in_calendar(db_session: Session) -> None:
    """Сид миграции 116: 01.01.2027 — «Новогодний старт» трёх систем."""
    seed = db_session.query(StartSpecialDate).filter(StartSpecialDate.start_date == date(2027, 1, 1)).one()
    assert seed.title == "Новогодний старт"
    assert sorted(seed.platforms) == ["five_verst", "runpark", "s95"]


def test_day_label() -> None:
    assert day_label(date(2026, 10, 31)) == "сб 31 октября"
    assert day_label(date(2027, 1, 1)) == "пт 1 января"
