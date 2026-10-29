"""Календарь стартов локации для планировщика забегов.

Даты, на которые можно запланировать старт, — только даты календаря
локации: субботы плюс особые даты из start_special_dates (их ведёт админ),
от сегодняшнего дня по Москве на год вперёд. Своих дат человек не задаёт:
план на «вторник, когда старта нет» никому не нужен (решение Дмитрия).

Локация здесь — идентичность (`catalog:<id>` / `location:<id>`), а её строки —
площадки систем. Строки берутся дёшево, без LocationCatalogIndex: блок
«Планируют пробежать» зовётся на каждый просмотр страницы локации, а полный
индекс читает все локации и все связки каталога.

Что не планируется (решения Дмитрия): parkrun; серии; локации на паузе и
«скоро открытие»; RunPark, который бегает по воскресеньям, — календарь пока
знает только субботы. «На паузе» — как видит сайт, а не только флаг строки:
флаг правило молчания ставит не всем (строки без координат оно пропускает),
и старая RunPark-строка держала бы «живой» локацию со страницей «Не
действует» (ревью 25.10.2026, CAL-1).

≈№ старта считается не прибавлением недель к последнему номеру (так делает
попап карты), а по слотам календаря: суббота и особая дата системы —
отдельные старты, и 01.01 сдвигает номер всех следующих суббот.

Отмена у систем — флаг «ближайший старт отменён» без даты. Дата у него одна
на всё (календарь, напоминание, «Старт отменён») — cancelled_start_date.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, tuple_
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
from app.services.location_activity_status import INACTIVE_AFTER_DAYS, STATUS_OVERRIDE_ACTIVE
from app.services.location_catalog_service import normalize_platform_code, resolve_location_display_name
from app.services.location_schedule_service import start_time_for_date

PLANNER_HORIZON_DAYS = 365
# Системы, старты которых планируются. parkrun нет: в России он не бегает с
# 2022 года, а зарубежные старты планировщику не нужны.
PLANNABLE_PLATFORMS = ("five_verst", "s95", "runpark")

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
# Время старта, если расписания нет: S95 и RunPark всегда в 9:00, у 5 вёрст
# это умолчание, пока описание локации не разобрано (как weather_service).
DEFAULT_START_TIME = "09:00"
# Опора ≈№ старше — номер не прогнозируем: локация давно молчит, и
# прибавленные недели дали бы номер, который не случится.
NUMBER_ANCHOR_MAX_AGE_DAYS = 56
# По скольким последним стартам RunPark решаем, субботняя ли это локация.
RUNPARK_WEEKDAY_SAMPLE = 10
_SATURDAY = 5
# Дата-заглушка у событий без даты в протоколе.
_EPOCH_GUARD = date(1970, 1, 1)

REASON_NOT_FOUND = "Локация не найдена"
REASON_PARKRUN = "Старты parkrun планировщик не показывает"
REASON_SERIES = "Это серия стартов в разных местах, а не одна локация"
REASON_UPCOMING = "Локация ещё не открылась"
REASON_PAUSED = "Локация сейчас не проводит старты"
REASON_SUNDAY = "Здесь бегают по воскресеньям, а планировщик пока знает только субботы"


@dataclass
class StartDate:
    """Старт в календаре идентичности."""

    date: date
    kind: str  # 'regular' | 'special'
    title: str | None
    # Система, которая проводит старт в эту дату, и её строка.
    platform_code: str
    location_id: UUID
    number_forecast: int | None
    start_time: str | None
    # Только у особых дат: был ли у локации старт в эту дату год назад.
    last_year: bool | None
    cancelled: bool
    cancel_reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "date": self.date,
            "kind": self.kind,
            "title": self.title,
            "platform_code": self.platform_code,
            "number_forecast": self.number_forecast,
            "start_time": self.start_time,
            "last_year": self.last_year,
            "cancelled": self.cancelled,
            "cancel_reason": self.cancel_reason,
        }


def moscow_today() -> date:
    """Сегодня по Москве. date.today() в контейнере — день по UTC, и с 00:00
    до 03:00 МСК прошедшая суббота ещё считалась бы будущей."""
    return datetime.now(MOSCOW_TZ).date()


_WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def day_label(value: date) -> str:
    """«сб 31 октября» — дата старта в текстах планировщика."""
    from app.services.activity_notification_service import _MONTHS_GENITIVE

    return f"{_WEEKDAYS_SHORT[value.weekday()]} {value.day} {_MONTHS_GENITIVE[value.month - 1]}"


def _platform_rank(code: str) -> int:
    from app.location_page_url import PLATFORM_ORDER

    return PLATFORM_ORDER.index(code) if code in PLATFORM_ORDER else len(PLATFORM_ORDER)


def parse_identity_key(identity_key: str | None) -> tuple[str, UUID] | None:
    """(«catalog» | «location», uuid) или None. Ключ приходит из адресной
    строки, мусор — штатный случай."""
    prefix, _, raw_id = (identity_key or "").strip().partition(":")
    if prefix not in ("catalog", "location"):
        return None
    try:
        return prefix, UUID(raw_id)
    except ValueError:
        return None


def normalize_identity_key(identity_key: str | None) -> str | None:
    """Ключ в единственной записи `<prefix>:<uuid>` (нижний регистр, с
    дефисами) или None, если это не ключ.

    UUID() принимает и «{…}», и запись без дефисов, и заглавные буквы, а ключи
    сравниваются строкой: сторож каталога (catalog_key_reference_counts),
    уникальный индекс планов и поиск конфликтов при объединении. План с ключом
    в иной записи они бы не узнали (ревью 25.10.2026, BE-5/SEC-4).
    """
    parsed = parse_identity_key(identity_key)
    if parsed is None:
        return None
    prefix, key_id = parsed
    return f"{prefix}:{key_id}"


def identity_rows(db: Session, identity_key: str) -> list[tuple[Location, str]]:
    """Строки (локация, код системы) идентичности, основная система первой.

    `location:<id>` — сама локация; `catalog:<id>` — связки узла: по
    location_id, а связка без него — локация по (система, слаг связки). По
    карте не фильтруем: планировать можно и строку вне официальной карты.
    """
    parsed = parse_identity_key(identity_key)
    if parsed is None:
        return []
    prefix, key_id = parsed
    base = db.query(Location, Platform.code).join(Platform, Location.platform_id == Platform.id)
    active_platform: str | None = None
    if prefix == "location":
        rows = base.filter(Location.id == key_id).all()
    else:
        links = (
            db.query(LocationCatalogLink.location_id, LocationCatalogLink.platform_id, LocationCatalogLink.external_key)
            .filter(LocationCatalogLink.catalog_id == key_id)
            .all()
        )
        ids = {location_id for location_id, _platform_id, _slug in links if location_id is not None}
        pairs = [(platform_id, slug) for location_id, platform_id, slug in links if location_id is None and slug]
        conditions = []
        if ids:
            conditions.append(Location.id.in_(ids))
        if pairs:
            conditions.append(tuple_(Location.platform_id, Location.external_key).in_(pairs))
        rows = base.filter(or_(*conditions)).all() if conditions else []
        active_platform = normalize_platform_code(
            db.query(LocationCatalog.active_platform).filter(LocationCatalog.id == key_id).scalar()
        )
    seen: set[UUID] = set()
    unique: list[tuple[Location, str]] = []
    for location, code in rows:
        if location.id not in seen:
            seen.add(location.id)
            unique.append((location, code))
    return sorted(
        unique,
        key=lambda item: (0 if item[1] == active_platform else 1, _platform_rank(item[1]), item[0].external_key),
    )


def identity_brief(db: Session, identity_key: str, rows: list[tuple[Location, str]]) -> tuple[str, str]:
    """(слаг, имя) идентичности — как на странице локации, но без полного индекса.

    Слаг — первая витринная строка действующей системы, иначе первая не-parkrun
    (identity_primary_slug). Имя — у строки действующей системы узла, а
    закрытый узел или узел без действующей системы — по имени самой строки
    (resolve_location_display_name).
    """
    from app.services.location_page_service import identity_primary_slug

    if not rows:
        return "", ""
    primary, primary_code = rows[0]
    catalog = None
    parsed = parse_identity_key(identity_key)
    if parsed is not None and parsed[0] == "catalog":
        catalog = db.get(LocationCatalog, parsed[1])
    active_name = None
    if catalog is not None:
        active = normalize_platform_code(catalog.active_platform)
        active_name = next((location.name for location, code in rows if code == active), None)
    name = resolve_location_display_name(
        catalog,
        platform_code=primary_code,
        source_name=primary.name,
        active_platform_name=active_name,
    )
    return identity_primary_slug(rows), name


def special_dates(db: Session, date_from: date, date_to: date) -> list[StartSpecialDate]:
    return (
        db.query(StartSpecialDate)
        .filter(StartSpecialDate.start_date >= date_from, StartSpecialDate.start_date <= date_to)
        .order_by(StartSpecialDate.start_date)
        .all()
    )


def _special_date_set(db: Session) -> set[date]:
    return {row[0] for row in db.query(StartSpecialDate.start_date).all()}


def _sunday_runpark_ids(db: Session, location_ids: list[UUID]) -> set[UUID]:
    """RunPark-строки, у которых старты обычно не в субботу.

    Мода дня недели по последним RUNPARK_WEEKDAY_SAMPLE нетестовым стартам,
    без особых дат: новогодний старт в будни моду бы размывал. Ничья — в пользу
    субботы: сомнение не повод прятать локацию. Стартов нет — суббота.
    """
    if not location_ids:
        return set()
    specials = _special_date_set(db)
    sunday: set[UUID] = set()
    for location_id in location_ids:
        dates = [
            row[0]
            for row in db.query(Event.event_date)
            .filter(
                Event.location_id == location_id,
                Event.is_test_event.is_(False),
                Event.event_date > _EPOCH_GUARD,
            )
            .order_by(Event.event_date.desc())
            .limit(RUNPARK_WEEKDAY_SAMPLE + len(specials))
            .all()
        ]
        sample = [value for value in dates if value not in specials][:RUNPARK_WEEKDAY_SAMPLE]
        if not sample:
            continue
        counts = Counter(value.weekday() for value in sample)
        if counts.get(_SATURDAY, 0) < max(counts.values()):
            sunday.add(location_id)
    return sunday


def _closed_catalog_location_ids(db: Session, rows: list[tuple[Location, str]]) -> set[UUID]:
    """Строки, чей узел каталога закрыт (`LocationCatalog.is_closed`).

    Связка узла — по location_id, а без него — по (система, слаг), как в
    identity_rows. Закрытый узел сайт показывает «Не действует» целиком, какие
    бы флаги ни стояли у его строк (LocationCatalogIndex._catalog_is_paused).
    """
    if not rows:
        return set()
    ids = {location.id for location, _code in rows}
    pairs = {(location.platform_id, location.external_key): location.id for location, _code in rows}
    links = (
        db.query(LocationCatalogLink.location_id, LocationCatalogLink.platform_id, LocationCatalogLink.external_key)
        .join(LocationCatalog, LocationCatalogLink.catalog_id == LocationCatalog.id)
        .filter(
            LocationCatalog.is_closed.is_(True),
            or_(
                LocationCatalogLink.location_id.in_(ids),
                tuple_(LocationCatalogLink.platform_id, LocationCatalogLink.external_key).in_(list(pairs)),
            ),
        )
        .all()
    )
    closed: set[UUID] = set()
    for location_id, platform_id, slug in links:
        if location_id is not None:
            if location_id in ids:
                closed.add(location_id)
        elif (platform_id, slug) in pairs:
            closed.add(pairs[(platform_id, slug)])
    return closed


def _last_start_dates(db: Session, location_ids: list[UUID]) -> dict[UUID, date]:
    """Последний нетестовый старт каждой строки — одним запросом."""
    if not location_ids:
        return {}
    rows = (
        db.query(Event.location_id, func.max(Event.event_date))
        .filter(
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
            Event.event_date > _EPOCH_GUARD,
        )
        .group_by(Event.location_id)
        .all()
    )
    return {location_id: last for location_id, last in rows}


def _site_inactive_reasons(
    db: Session, candidates: list[tuple[Location, str]], today: date
) -> dict[UUID, str]:
    """Строки, которые по флагам живы, а сайт показывает «Не действует» или
    «скоро открытие», — с причиной.

    Правила те же, что у статуса сайта (location_activity_status):
    - узел каталога закрыт — пауза;
    - стартов не было ни разу — «ещё не открылась»: звать на пустой парк нельзя;
    - последний старт старше INACTIVE_AFTER_DAYS — пауза. Флаг is_paused
      ставит ночное правило молчания, но строки без координат оно пропускает
      (из-за разъездных серий), и у старых RunPark-строк флага нет никогда:
      «Чертаново» с последним стартом RunPark в 2022 году планировалось, а
      страница локации писала «Не действует».
    Ручное «Действует» (status_override) сильнее дат — как в правиле молчания.
    Отмена ближайшего старта паузой не считается — так же, как у сайта
    (LocationCatalogIndex.is_paused): отменили одну субботу, а локация жива.
    """
    if not candidates:
        return {}
    closed = _closed_catalog_location_ids(db, candidates)
    last_start = _last_start_dates(db, [location.id for location, _code in candidates])
    cutoff = today - timedelta(days=INACTIVE_AFTER_DAYS)
    reasons: dict[UUID, str] = {}
    for location, _code in candidates:
        cancelled = bool(location.is_cancelled)
        manual_active = location.status_override == STATUS_OVERRIDE_ACTIVE
        last = last_start.get(location.id)
        if location.id in closed and not cancelled:
            reasons[location.id] = REASON_PAUSED
        elif last is None and not manual_active:
            reasons[location.id] = REASON_UPCOMING
        elif last is not None and last < cutoff and not manual_active and not cancelled:
            reasons[location.id] = REASON_PAUSED
    return reasons


def plannable_rows_with_reason(
    db: Session, rows: list[tuple[Location, str]], *, today: date | None = None
) -> tuple[list[tuple[Location, str]], str | None]:
    """Строки, старты которых можно планировать, и почему нельзя, если таких нет.

    Причина — у первой (основной) строки идентичности: у смешанной точки
    «parkrun-эпоха → 5 вёрст» на паузе 5 вёрст, а не parkrun.

    Мёртвая строка выпадает, даже если у идентичности есть живая: иначе
    особая дата только для её системы (RunPark) встала бы в календарь живой
    5 вёрст.
    """
    if not rows:
        return [], REASON_NOT_FOUND
    reasons: dict[UUID, str] = {}
    candidates: list[tuple[Location, str]] = []
    for location, code in rows:
        if code not in PLANNABLE_PLATFORMS:
            reasons[location.id] = REASON_PARKRUN
        elif location.is_series:
            reasons[location.id] = REASON_SERIES
        elif location.is_upcoming:
            reasons[location.id] = REASON_UPCOMING
        elif location.is_paused and not location.is_cancelled:
            # Отмена сильнее паузы, как у сайта: молчание с отменой — это
            # «Отмена старта» на странице, а не «Не действует».
            reasons[location.id] = REASON_PAUSED
        else:
            candidates.append((location, code))
    reasons.update(_site_inactive_reasons(db, candidates, today or moscow_today()))
    candidates = [(location, code) for location, code in candidates if location.id not in reasons]
    sunday = _sunday_runpark_ids(db, [location.id for location, code in candidates if code == "runpark"])
    for location_id in sunday:
        reasons[location_id] = REASON_SUNDAY
    plannable = [(location, code) for location, code in candidates if location.id not in sunday]
    if plannable:
        return plannable, None
    # Основная причина — у первой строки не-parkrun, если такая есть.
    for location, code in rows:
        if code != "parkrun" and location.id in reasons:
            return [], reasons[location.id]
    return [], reasons.get(rows[0][0].id, REASON_PARKRUN)


def plannable_rows(
    db: Session, rows: list[tuple[Location, str]], *, today: date | None = None
) -> list[tuple[Location, str]]:
    return plannable_rows_with_reason(db, rows, today=today)[0]


def _next_saturday(today: date) -> date:
    return today + timedelta(days=(_SATURDAY - today.weekday()) % 7)


# Последняя дата, которую календарь стартов обходит шагом в неделю.
_LAST_CALENDAR_DATE = date.max - timedelta(days=7)


@dataclass(frozen=True)
class StartDay:
    """День старта вообще, без локации: суббота или особая дата из админки."""

    date: date
    kind: str  # 'regular' | 'special'
    title: str | None


def start_day(db: Session, on_date: date) -> StartDay | None:
    """День старта или None: суббота либо особая дата (любой системы).

    На него ставится «Не бегаю»: отметка о дне, а не о локации, поэтому
    системы особой даты не важны.
    """
    special = db.query(StartSpecialDate).filter(StartSpecialDate.start_date == on_date).one_or_none()
    if special is not None:
        return StartDay(date=on_date, kind="special", title=special.title)
    if on_date.weekday() == _SATURDAY:
        return StartDay(date=on_date, kind="regular", title=None)
    return None


def nearest_start_day(
    db: Session,
    today: date | None = None,
    *,
    platform_codes: Collection[str] | None = None,
    planned: Collection[date] = (),
) -> StartDay:
    """Ближайший день старта по Москве: эта суббота (в саму субботу — до
    конца дня) или особая дата раньше неё.

    «Где я в ближайшую субботу» (решение Дмитрия 26.10.2026) — карточка
    «Ближайший план» спрашивает про этот день, а не про ближайший план: план
    через месяц не ответ на «где я в эту субботу». Строка профиля — уже о
    ближайшем публичном плане на любую дату (27.10.2026).

    Особая дата — только систем человека (`platform_codes`; None — без
    отбора) или та, на которую у него есть план (`planned`): праздник S95 в
    среду бегуну 5 вёрст не ближайший старт — как и в календаре локации, где
    особые даты чужих систем не появляются (ревью 26.10.2026, PLAN-REV-1).
    Особая суббота чужой системы — обычная суббота.
    """
    today = today or moscow_today()
    saturday = _next_saturday(today)
    codes = set(platform_codes) if platform_codes is not None else None
    for special in special_dates(db, today, saturday):
        if codes is None or special.start_date in planned or codes & set(special.platforms or []):
            return StartDay(date=special.start_date, kind="special", title=special.title)
    return StartDay(date=saturday, kind="regular", title=None)


def _year_ago(value: date) -> date | None:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:  # 29 февраля
        return None


def cancelled_start_date(db: Session, history_ids: list[UUID], platform_code: str, today: date) -> date:
    """Какой старт отменяет флаг «ближайший старт отменён» строки системы
    `platform_code`, если смотреть из `today` (день по поясу локации).

    Источники датируют флаг субботой: у 5 вёрст в реестре «отмена на
    ближайшую субботу», наблюдатель S95 спрашивает «побегут ли в ближайшую
    субботу», общая рассылка «Отмена старта» тоже пишет субботу. Особая дата
    до субботы забирает отмену себе, только если её старт вероятен — у строк
    идентичности (`history_ids`) год назад в эту дату был старт, — и она ещё
    впереди. Иначе локация, которая 01.01 не бегает, отменив 02.01, получила бы
    «старт 01.01 отменён», а планирующие 02.01 не узнали бы ничего (ревью
    25.10.2026, NT-1). В день самой особой даты флаг — уже о субботе.

    Ни от какого окна календаря дата не зависит: и календарь, и напоминание,
    и «Старт отменён» спрашивают её здесь (NT-2 — раньше метка «отменён»
    переезжала между 01.01 и 02.01 в зависимости от того, с какой даты
    загрузили календарь).
    """
    saturday = _next_saturday(today)
    candidates = [
        special.start_date
        for special in special_dates(db, today, saturday)
        if today < special.start_date and platform_code in (special.platforms or [])
    ]
    if candidates:
        year_ago = {value: _year_ago(value) for value in candidates}
        had = _had_start(db, history_ids, [value for value in year_ago.values() if value is not None])
        for value in candidates:
            if year_ago[value] is not None and year_ago[value] in had:
                return value
    return saturday


def _anchors(db: Session, location_ids: list[UUID]) -> dict[UUID, tuple[date, int]]:
    """Последний нумерованный нетестовый старт каждой строки: (дата, номер)."""
    if not location_ids:
        return {}
    numbered = (
        Event.location_id.in_(location_ids),
        Event.is_test_event.is_(False),
        Event.event_number.isnot(None),
        Event.event_date > _EPOCH_GUARD,
    )
    latest = (
        db.query(Event.location_id.label("location_id"), func.max(Event.event_date).label("last_date"))
        .filter(*numbered)
        .group_by(Event.location_id)
        .subquery()
    )
    # Один старт на локацию в день (uq_events_location_event_date) — джойн по
    # дате даёт ровно одну строку.
    rows = (
        db.query(Event.location_id, Event.event_date, Event.event_number)
        .join(latest, (Event.location_id == latest.c.location_id) & (Event.event_date == latest.c.last_date))
        .filter(*numbered)
        .all()
    )
    return {location_id: (event_date, int(number)) for location_id, event_date, number in rows}


def _schedules(db: Session, location_ids: list[UUID]) -> dict[UUID, list[dict[str, object]]]:
    if not location_ids:
        return {}
    rows = (
        db.query(LocationDescription.location_id, LocationDescription.schedule_parsed)
        .filter(LocationDescription.location_id.in_(location_ids))
        .all()
    )
    return {location_id: schedule or [] for location_id, schedule in rows}


def _had_start(db: Session, location_ids: list[UUID], dates: list[date]) -> set[date]:
    """Даты из `dates`, в которые у строк был нетестовый старт."""
    if not location_ids or not dates:
        return set()
    rows = (
        db.query(Event.event_date)
        .filter(
            Event.location_id.in_(location_ids),
            Event.event_date.in_(dates),
            Event.is_test_event.is_(False),
        )
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def start_dates(
    db: Session,
    rows: list[tuple[Location, str]],
    *,
    date_from: date,
    date_to: date,
    today: date | None = None,
    history_rows: list[tuple[Location, str]] | None = None,
) -> list[StartDate]:
    """Старты идентичности в [date_from, date_to]: субботы и особые даты её систем.

    `rows` — строки, которые планируются (plannable_rows). Систему даты даёт
    первая подходящая строка: суббота — первая строка, особая дата — первая
    строка из систем этой даты.

    `history_rows` — все строки идентичности (по умолчанию `rows`): «в прошлом
    году старт был» смотрит и строки, которых в планируемых уже нет. У
    Раменского 01.01.2026 бежали в 5 вёрст, а сейчас 5 вёрст на паузе, и по
    одним планируемым строкам 01.01.2027 выходило «старт не подтверждён»
    (ревью 25.10.2026, CAL-4).
    """
    # Шаг по субботам — неделя: дальше date.max - 7 дней он переполнился бы
    # (запрос с датами у края диапазона отвечал 500).
    date_to = min(date_to, _LAST_CALENDAR_DATE)
    if not rows or date_to < date_from:
        return []
    today = today or moscow_today()
    history_ids = [location.id for location, _code in (history_rows if history_rows is not None else rows)]
    if not history_ids:
        history_ids = [location.id for location, _code in rows]
    codes = {code for _location, code in rows}
    # Особые даты нужны и раньше date_from: ≈№ считает слоты от опорного
    # старта, а он бывает и до начала окна.
    anchor_by_row = _anchors(db, [location.id for location, _code in rows])
    earliest_anchor = min((anchor for anchor, _number in anchor_by_row.values()), default=date_from)
    specials_all = special_dates(db, min(earliest_anchor, date_from), date_to)
    specials_by_date = {item.start_date: item for item in specials_all if codes & set(item.platforms or [])}

    # «В прошлом году старт был» — по всем строкам идентичности.
    last_year_dates = {
        special_date: _year_ago(special_date) for special_date in specials_by_date if special_date >= today
    }
    had = _had_start(
        db,
        history_ids,
        [value for value in last_year_dates.values() if value is not None],
    )
    confirmed_specials = {
        special_date
        for special_date, year_ago in last_year_dates.items()
        if year_ago is not None and year_ago in had
    }

    schedules = _schedules(db, [location.id for location, code in rows if code == "five_verst"])

    def row_for(special: StartSpecialDate | None) -> tuple[Location, str] | None:
        if special is None:
            return rows[0]
        allowed = set(special.platforms or [])
        return next(((location, code) for location, code in rows if code in allowed), None)

    # Отменённый старт каждой отменённой строки — один на все окна календаря.
    cancelled_on = {
        location.id: cancelled_start_date(db, history_ids, code, today)
        for location, code in rows
        if location.is_cancelled
    }

    def forecast(location: Location, code: str, on_date: date) -> int | None:
        anchor = anchor_by_row.get(location.id)
        if anchor is None:
            return None
        anchor_date, anchor_number = anchor
        if (today - anchor_date).days > NUMBER_ANCHOR_MAX_AGE_DAYS:
            return None
        if on_date <= anchor_date:
            return anchor_number if on_date == anchor_date else None
        cancelled = cancelled_on.get(location.id)
        if cancelled == on_date:
            # Как попап карты: у отменённого старта номера не будет, и ≈№ на
            # нём вводил бы в заблуждение.
            return None
        # Слоты строго между опорой и датой: каждая суббота и будущие особые
        # даты системы, которые в прошлом году действительно были (прошедшие
        # уже вошли в номер опоры). Сама дата — плюс один: если старт будет,
        # номер у него следующий.
        slots = 0
        cursor = _next_saturday(anchor_date + timedelta(days=1))
        while cursor < on_date:
            slots += 1
            cursor += timedelta(days=7)
        for special_date, special in specials_by_date.items():
            if (
                anchor_date < special_date < on_date
                and special_date.weekday() != _SATURDAY
                and special_date in confirmed_specials
                and code in (special.platforms or [])
            ):
                slots += 1
        # Отменённый старт номер не расходует: следующая суббота получит тот,
        # что достался бы ему (ревью 25.10.2026, BE-1). Вычитаем, только если
        # он был посчитан слотом выше.
        if (
            cancelled is not None
            and anchor_date < cancelled < on_date
            and (cancelled.weekday() == _SATURDAY or cancelled in confirmed_specials)
        ):
            slots -= 1
        return anchor_number + slots + 1

    candidates: set[date] = set(specials_by_date)
    cursor = _next_saturday(date_from)
    while cursor <= date_to:
        candidates.add(cursor)
        cursor += timedelta(days=7)

    result: list[StartDate] = []
    for on_date in sorted(candidates):
        if on_date < date_from or on_date > date_to:
            continue
        special = specials_by_date.get(on_date)
        row = row_for(special)
        if row is None and special is not None and on_date.weekday() == _SATURDAY:
            # Особая суббота не для этих систем — обычная суббота.
            special, row = None, rows[0]
        if row is None:
            continue
        location, code = row
        if special is not None:
            start_time = None
        else:
            scheduled = start_time_for_date(schedules.get(location.id), on_date) if code == "five_verst" else None
            start_time = scheduled.strftime("%H:%M") if scheduled is not None else DEFAULT_START_TIME
        cancelled = bool(location.is_cancelled) and on_date == cancelled_on.get(location.id)
        result.append(
            StartDate(
                date=on_date,
                kind="special" if special is not None else "regular",
                title=special.title if special is not None else None,
                platform_code=code,
                location_id=location.id,
                number_forecast=forecast(location, code, on_date),
                start_time=start_time,
                last_year=(on_date in confirmed_specials) if special is not None else None,
                cancelled=cancelled,
                cancel_reason=(location.cancel_reason if cancelled else None),
            )
        )
    return result
