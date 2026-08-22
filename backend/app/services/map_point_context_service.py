"""Что показывает попап точки на карте сверх статистики визитов.

Три вещи, за которыми человек лезет в попап, когда планирует субботу (просьба
Дмитрия 22.08.2026):

* **Номер ближайшего старта.** Прогноз тот же, что в планировании «Нумератора»:
  последний известный старт площадки плюс неделя. Точного расписания у нас нет,
  локация может пропустить субботу — поэтому число всегда со знаком «≈».
* **Даст ли этот номер +1 в «Нумераторе».** Ответ двойной, и это не придирка:
  клетку челленджа закрывает пробежка в ЛЮБОЙ системе (сквозной зачёт), но
  фильтр систем на странице достижений считает тот же челлендж по одной системе.
  Старт №137 на s95 у человека, который №137 уже брал на 5 вёрстах, в сквозном
  зачёте не даст ничего, а в зачёте s95 — даст.
* **Сколько отсюда до дома** — и куда идти, если домашняя локация выбрана
  автоматически и человек с ней не согласен.

Всё, кроме прогноза номера, — личные данные: анониму отдаётся только он.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Event, Location, LocationCatalogLink, Platform, User
from app.services.achievements_service import START_NUMBER_RANGES, START_NUMBER_TITLES
from app.services.home_distance_service import location_distance_from_home
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_map_service import MAP_HISTORIC_PLATFORM, map_location_filter

_EPOCH_GUARD = date(1970, 1, 1)

# Докуда откручиваем прогноз вперёд от последнего старта. Площадка, молчащая
# два месяца, ещё не помечена паузой (правило молчания срабатывает на 100 дне),
# но обещать её «ближайший старт» уже нельзя — честнее не показывать строку.
MAX_FORECAST_WEEKS = 8


@dataclass(frozen=True)
class NextStart:
    platform_code: str
    number: int
    event_date: date
    # Сколько недель откручено от последнего старта: 1 — площадка идёт вровень,
    # больше — она пропускала субботы, и прогноз тем шатче, чем число выше.
    weeks_ahead: int


def _challenge_for_number(number: int) -> tuple[str, str] | None:
    """Какой «Нумератор» закрывает этот номер. None — номер вне обоих диапазонов."""
    for code, (low, high) in START_NUMBER_RANGES.items():
        if low <= number <= high:
            return code, START_NUMBER_TITLES[code]
    return None


def _locations_for_identity(
    db: Session, identity_key: str
) -> list[tuple[Location, str]]:
    """Строки локаций (по одной на систему), из которых сложена точка на карте.

    Ключ идентичности собирает `LocationCatalogIndex.canonical_identity_key`:
    у площадки в каталоге это `catalog:<id>`, у одиночки — `location:<id>`.

    Отбор — тот же `map_location_filter()`, что у самой карты. Без него в выборку
    попадали служебные строки, которых на точке нет: у «Ангарских прудов»
    (parkrun + s95 на карте) всплывал ещё и runpark мимо официального реестра, и
    попап предлагал старт системы, которой на этой площадке не существует.
    """
    prefix, _, raw_id = identity_key.partition(":")
    try:
        key_id = UUID(raw_id)
    except ValueError:
        return []
    query = (
        db.query(Location, Platform.code)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(map_location_filter())
    )
    if prefix == "location":
        query = query.filter(Location.id == key_id)
    elif prefix == "catalog":
        query = (
            query.join(LocationCatalogLink, LocationCatalogLink.location_id == Location.id)
            .filter(LocationCatalogLink.catalog_id == key_id)
            .distinct()
        )
    else:
        return []
    return [(location, platform_code) for location, platform_code in query.all()]


def _forecast_next_starts(
    db: Session,
    rows: list[tuple[Location, str]],
    *,
    today: date,
) -> list[NextStart]:
    """Прогноз ближайшего старта по каждой действующей системе площадки.

    parkrun пропускаем целиком: из России он ушёл в 2022, и «ближайшего старта»
    у его строк не будет. Паузу и отмену тоже: про них попап говорит отдельной
    строкой, и предлагать поверх этого номер — вводить в заблуждение.
    """
    live: dict[UUID, tuple[Location, str]] = {}
    for location, platform_code in rows:
        if platform_code == MAP_HISTORIC_PLATFORM:
            continue
        if location.is_paused or location.is_cancelled:
            continue
        live[location.id] = (location, platform_code)
    if not live:
        return []

    last_events = (
        db.query(Event.location_id, Event.event_number, Event.event_date)
        .filter(
            Event.location_id.in_(live.keys()),
            Event.is_test_event.is_(False),
            Event.event_number.isnot(None),
            Event.event_date > _EPOCH_GUARD,
        )
        .order_by(Event.location_id, Event.event_date.desc())
        .all()
    )

    seen: set[UUID] = set()
    forecasts: list[NextStart] = []
    for location_id, event_number, event_date in last_events:
        if location_id in seen:
            continue
        seen.add(location_id)
        # Номер растёт вместе с датой: пропущенная суббота сдвигает и то и другое,
        # поэтому откручиваем неделями, пока прогноз не окажется в будущем.
        weeks = 1
        while event_date + timedelta(weeks=weeks) < today and weeks <= MAX_FORECAST_WEEKS:
            weeks += 1
        if weeks > MAX_FORECAST_WEEKS:
            continue
        forecasts.append(
            NextStart(
                platform_code=live[location_id][1],
                number=event_number + weeks,
                event_date=event_date + timedelta(weeks=weeks),
                weeks_ahead=weeks,
            )
        )
    forecasts.sort(key=lambda item: (item.event_date, item.platform_code))
    return forecasts


def _my_start_numbers(db: Session, user_id: UUID) -> tuple[set[int], dict[str, set[int]]]:
    """Номера стартов, которые человек уже брал: сквозной набор и по системам.

    Считаем ровно как челлендж (см. `_collect_run_rows`): без тестовых стартов и
    без вторичных строк кросс-связки — одна и та же пробежка, найденная в двух
    системах, не должна закрывать номер дважды.
    """
    from app.models import PlatformLink, RunResult
    from app.services.personal_record_service import user_secondary_crosslinked_run_ids

    query = (
        db.query(Event.event_number, Platform.code, RunResult.id)
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.is_test_event.is_(False),
            Event.event_number.isnot(None),
            Event.event_date > _EPOCH_GUARD,
        )
    )
    secondary_ids = user_secondary_crosslinked_run_ids(db, user_id)
    if secondary_ids:
        query = query.filter(RunResult.id.notin_(secondary_ids))

    overall: set[int] = set()
    by_platform: dict[str, set[int]] = {}
    for event_number, platform_code, _run_id in query.all():
        overall.add(event_number)
        by_platform.setdefault(platform_code, set()).add(event_number)
    return overall, by_platform


def _coordinates_for_point(
    catalog_index: LocationCatalogIndex,
    identity_key: str,
    rows: list[tuple[Location, str]],
) -> tuple[float | None, float | None]:
    """Координаты точки — как их берёт сама карта: сперва каталог, потом строка
    локации. Без второго шага площадки без каталожной точки (зарубежные s95 и
    RunPark) на карте есть, а расстояние до дома у них выходило «—»."""
    latitude, longitude = catalog_index.coordinates_for_identity_key(identity_key)
    if latitude is not None and longitude is not None:
        return latitude, longitude
    for location, _platform_code in rows:
        if location.latitude is not None and location.longitude is not None:
            return location.latitude, location.longitude
    return None, None


def build_map_point_context(
    db: Session,
    user: User | None,
    identity_key: str,
    *,
    today: date | None = None,
    catalog_index: LocationCatalogIndex | None = None,
) -> dict[str, object]:
    """Личный контекст одной точки карты. Дёргается по клику, не пачкой:
    выкладывать это в общий payload карты нельзя — там три тысячи точек."""
    today = today or date.today()
    catalog_index = catalog_index or LocationCatalogIndex(db)
    rows = _locations_for_identity(db, identity_key)

    overall_done: set[int] = set()
    platform_done: dict[str, set[int]] = {}
    if user is not None:
        overall_done, platform_done = _my_start_numbers(db, user.id)

    next_starts: list[dict[str, object]] = []
    for forecast in _forecast_next_starts(db, rows, today=today):
        challenge = _challenge_for_number(forecast.number)
        entry: dict[str, object] = {
            "platform_code": forecast.platform_code,
            "number": forecast.number,
            "date": forecast.event_date.isoformat(),
            "weeks_ahead": forecast.weeks_ahead,
            "challenge_code": challenge[0] if challenge else None,
            "challenge_title": challenge[1] if challenge else None,
            "plus_one_overall": None,
            "plus_one_platform": None,
        }
        if user is not None and challenge is not None:
            entry["plus_one_overall"] = forecast.number not in overall_done
            entry["plus_one_platform"] = forecast.number not in platform_done.get(
                forecast.platform_code, set()
            )
        next_starts.append(entry)

    home: dict[str, object] | None = None
    if user is not None:
        home = location_distance_from_home(
            db,
            user,
            identity_key,
            _coordinates_for_point(catalog_index, identity_key, rows),
            catalog_index=catalog_index,
        )

    return {
        "identity_key": identity_key,
        "authenticated": user is not None,
        "next_starts": next_starts,
        "home_distance": home,
    }
