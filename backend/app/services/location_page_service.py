"""Публичная страница локации: резолв slug, сводные цифры, гистограмма, инфо-карточка.

Идентичность локации сквозь платформы — canonical_identity_key из
LocationCatalogIndex (кросслинки каталога). Публичный slug страницы —
external_key «основной» платформы идентичности (active_platform каталога,
иначе первая по PLATFORM_ORDER). Резолвер принимает external_key любой
платформы, в том числе в нормализованной форме (readovsky-park ≡ readovskypark).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast
from uuid import UUID

import redis
from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.activity_url import resolve_activity_url
from app.core.redis_client import get_redis_client
from app.location_page_url import PLATFORM_ORDER, location_page_url
from app.models import (
    Event,
    EventCrosslink,
    EventSummary,
    Location,
    LocationCatalog,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services.location_catalog_service import (
    LocationCatalogIndex,
    normalize_location_slug,
    normalize_platform_code,
    resolve_location_display_name,
)
from app.time_format import format_finish_time_display

HISTOGRAM_BIN_SEC = 10
# Индекс локаций — тяжёлая агрегация (~35 тыс. событий + join по протоколам
# на прод-объёме), а меняется только раз в неделю после субботнего синка.
# TTL, а не точечная инвалидация: синк идёт множеством независимых batch-джоб
# по трём платформам — вешать инвалидацию на каждую было бы куда инвазивнее,
# чем оправдывает выигрыш (эти цифры не обязаны быть live).
LOCATIONS_INDEX_CACHE_KEY = "locations:index:v1"
LOCATIONS_INDEX_CACHE_TTL_SECONDS = 3 * 60 * 60
# Страница/журнал/рейтинги одной локации — тоже тяжёлые (resolve_location_identity
# перечитывает ВСЕ локации + весь каталог на каждый вызов, плюс десяток
# отдельных запросов на подсчёты). Тот же TTL-подход: синк раз в неделю,
# смысла в точечной инвалидации нет. Отдельно от индекса, потому что меняется
# per-slug, а не одним блобом на весь каталог.
LOCATION_PAGE_CACHE_TTL_SECONDS = 3 * 60 * 60
# Незачётные статусы протоколов не влияют на finish_time (он у них NULL),
# поэтому отдельного фильтра по status нет: гистограмма и рекорды строятся
# только по строкам с известным временем.
_AGE_RANGE_RE = re.compile(r"(\d{1,2})\s*[-–—]\s*(\d{1,2})")
_AGE_PLUS_RE = re.compile(r"(\d{2,3})\s*\+")


def _platform_order_index(code: str) -> int:
    try:
        return PLATFORM_ORDER.index(code)
    except ValueError:
        return len(PLATFORM_ORDER)


def normalize_age_group(age_category: str | None) -> str | None:
    """«М18-24», «SM25-29», «VM35-39» → «18–24»; «М75+» → «75+»."""
    if not age_category:
        return None
    match = _AGE_RANGE_RE.search(age_category)
    if match:
        return f"{int(match.group(1))}–{int(match.group(2))}"
    match = _AGE_PLUS_RE.search(age_category)
    if match:
        return f"{int(match.group(1))}+"
    return None


@dataclass
class _PlatformStat:
    first_event_date: date
    last_event_date: date
    events_count: int


@dataclass
class LocationIdentity:
    identity_key: str
    catalog: LocationCatalog | None
    locations: list[tuple[Location, str]]  # (location, platform_code)
    slug: str
    name: str
    # Уже построенный на резолве идентичности — переиспользуем ниже вместо
    # повторного полного скана каталога (LocationCatalogIndex._load() — join
    # по всем location_catalog_links, дорого повторять в одном запросе).
    catalog_index: LocationCatalogIndex


def _identity_display_name(
    catalog: LocationCatalog | None,
    locations: list[tuple[Location, str]],
) -> str:
    primary_location, primary_code = locations[0]
    return resolve_location_display_name(
        catalog,
        platform_code=primary_code,
        source_name=primary_location.name,
    )


def _sort_identity_locations(
    catalog: LocationCatalog | None,
    locations: list[tuple[Location, str]],
) -> list[tuple[Location, str]]:
    """Основная платформа идентичности первой: active_platform, потом PLATFORM_ORDER."""
    active = normalize_platform_code(catalog.active_platform) if catalog is not None else None

    def sort_key(item: tuple[Location, str]) -> tuple[int, int, str]:
        _location, code = item
        return (0 if code == active else 1, _platform_order_index(code), code)

    return sorted(locations, key=sort_key)


def resolve_location_identity(db: Session, slug: str) -> LocationIdentity | None:
    requested = (slug or "").strip().lower()
    if not requested:
        return None
    requested_normalized = normalize_location_slug(requested)

    rows = db.query(Location, Platform.code).join(Platform, Location.platform_id == Platform.id).all()
    catalog_index = LocationCatalogIndex(db)

    identity_locations: dict[str, list[tuple[Location, str]]] = {}
    matched_keys: list[str] = []
    for location, platform_code in rows:
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        identity_locations.setdefault(identity_key, []).append((location, platform_code))
        external = location.external_key.strip().lower()
        if external == requested or (
            requested_normalized and normalize_location_slug(external) == requested_normalized
        ):
            matched_keys.append(identity_key)

    if not matched_keys:
        return None

    # Один slug теоретически может встретиться в двух несвязанных локациях —
    # берём идентичность, чья лучшая платформа раньше в PLATFORM_ORDER.
    def identity_rank(key: str) -> int:
        return min(_platform_order_index(code) for _loc, code in identity_locations[key])

    identity_key = sorted(set(matched_keys), key=identity_rank)[0]
    catalog = catalog_index.get_for_identity_key(identity_key)
    locations = _sort_identity_locations(catalog, identity_locations[identity_key])
    return LocationIdentity(
        identity_key=identity_key,
        catalog=catalog,
        locations=locations,
        slug=locations[0][0].external_key.strip().lower(),
        name=_identity_display_name(catalog, locations),
        catalog_index=catalog_index,
    )


def _gender_expression(
    platform_code_col: Any,
    participant_extra_col: Any,
    age_category_col: Any,
    participant_age_category_col: Any,
) -> Any:
    """SQL-выражение пола финишёра.

    five_verst/runpark — буква в age_category результата протокола. parkrun
    протокол своей age_category не даёт (в run_results там age-grade %, а не
    категория) — пол берётся из participants.age_category («SM30-34» —
    вторая буква; ~99.9% строк после бэкфилла профилей). s95 — из
    participants.profile_extra (см. gender_position_service).
    """
    return case(
        (
            platform_code_col == "five_verst",
            case(
                (func.substr(age_category_col, 1, 1) == "М", "male"),
                (func.substr(age_category_col, 1, 1) == "Ж", "female"),
                else_=None,
            ),
        ),
        (
            platform_code_col == "runpark",
            case(
                (func.substr(age_category_col, 2, 1) == "M", "male"),
                (func.substr(age_category_col, 2, 1) == "W", "female"),
                else_=None,
            ),
        ),
        (
            platform_code_col == "parkrun",
            case(
                (func.substr(participant_age_category_col, 2, 1) == "M", "male"),
                (func.substr(participant_age_category_col, 2, 1) == "W", "female"),
                else_=None,
            ),
        ),
        (
            platform_code_col == "s95",
            participant_extra_col["platform_codes"]["gender"].astext,
        ),
        else_=None,
    )


def _dedupe_crosslinked_events(db: Session, event_ids: list[UUID]) -> set[UUID]:
    """Убрать вторичные события кросслинков (один физический старт в двух протоколах)."""
    kept = set(event_ids)
    if not event_ids:
        return kept
    links = (
        db.query(EventCrosslink.primary_event_id, EventCrosslink.secondary_event_id)
        .filter(EventCrosslink.secondary_event_id.in_(event_ids))
        .all()
    )
    for primary_id, secondary_id in links:
        if primary_id in kept and secondary_id in kept:
            kept.discard(secondary_id)
    return kept


def _course_record(
    db: Session,
    event_ids: list[UUID],
    gender: str,
) -> dict[str, object] | None:
    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    row = (
        db.query(
            RunResult.finish_time_sec,
            Participant.display_name,
            Event.event_date,
            Platform.code,
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .filter(
            RunResult.event_id.in_(event_ids),
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
            gender_expr == gender,
        )
        .order_by(RunResult.finish_time_sec.asc())
        .first()
    )
    if row is None:
        return None
    finish_time_sec, display_name, event_date, platform_code = row
    return {
        "finish_time_sec": finish_time_sec,
        "finish_time_display": format_finish_time_display(finish_time_sec),
        "runner_name": display_name,
        "event_date": event_date,
        "platform_code": platform_code,
    }


def _histogram_rows(db: Session, event_ids: list[UUID]) -> list[dict[str, object]]:
    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    bin_expr = (RunResult.finish_time_sec // HISTOGRAM_BIN_SEC) * HISTOGRAM_BIN_SEC
    rows = (
        db.query(
            bin_expr.label("start_sec"),
            gender_expr.label("gender"),
            RunResult.age_category,
            func.count().label("count"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .filter(
            RunResult.event_id.in_(event_ids),
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
        )
        .group_by("start_sec", "gender", RunResult.age_category)
        .all()
    )
    aggregated: dict[tuple[int, str | None, str | None], int] = {}
    for start_sec, gender, age_category, count in rows:
        if gender not in ("male", "female"):
            gender = None
        key = (int(start_sec), gender, normalize_age_group(age_category))
        aggregated[key] = aggregated.get(key, 0) + int(count)
    return [
        {"start_sec": start_sec, "gender": gender, "age_group": age_group, "count": count}
        for (start_sec, gender, age_group), count in sorted(aggregated.items(), key=lambda item: item[0][0])
    ]


def _location_event_ids(db: Session, location_ids: list[UUID]) -> list[UUID]:
    """ID всех нетестовых событий идентичности с дедупом кросслинков."""
    rows = db.query(Event.id).filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False)).all()
    all_ids = [row[0] for row in rows]
    kept = _dedupe_crosslinked_events(db, all_ids)
    return [event_id for event_id in all_ids if event_id in kept]


def build_location_leaders(
    db: Session, slug: str, *, limit: int = 20, use_cache: bool = True
) -> dict[str, object] | None:
    cache_key = f"locations:leaders:v1:{slug.strip().lower()}"
    if use_cache:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_leaders(db, slug, limit=limit)

    if use_cache and payload is not None:
        _write_json_cache(cache_key, payload, LOCATION_PAGE_CACHE_TTL_SECONDS)
    return payload


def _platform_link_join() -> Any:
    """Тот же принцип, что в admin_users_service._participant_join: надёжный ключ
    между participants и platform_links — (platform_id, external_user_id),
    а не platform_links.participant_id (не всегда проставлен)."""
    return and_(
        PlatformLink.platform_id == Participant.platform_id,
        PlatformLink.external_user_id == Participant.external_user_id,
    )


def _compute_location_leaders(db: Session, slug: str, *, limit: int = 20) -> dict[str, object] | None:
    """Рейтинги внутри локации: топ по пробежкам и топ по волонтёрствам.

    Перенос смысла Grafana-дашборда «Рейтинг участников и волонтёров внутри
    локации». Один человек с единым профилем на сайте (привязал аккаунты
    нескольких систем через platform_links) считается ОДНОЙ строкой суммарно
    по всем платформам — группировка идёт по coalesce(platform_links.user_id,
    participant_id). Без привязки объединить разные participants разных
    систем нельзя (это правда разные внешние аккаунты) — такие остаются
    отдельными строками.
    """
    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None
    event_ids = _location_event_ids(db, [location.id for location, _code in identity.locations])

    runners: list[dict[str, object]] = []
    volunteers: list[dict[str, object]] = []
    if event_ids:
        time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
        display_name = func.max(func.coalesce(User.display_name, Participant.display_name))
        runner_group_key = func.coalesce(PlatformLink.user_id, RunResult.participant_id)
        runner_rows = (
            db.query(
                display_name.label("name"),
                func.count(func.distinct(RunResult.event_id)).label("runs"),
                func.min(case((time_ok, RunResult.finish_time_sec))).label("best"),
            )
            .join(Participant, RunResult.participant_id == Participant.id)
            .outerjoin(PlatformLink, _platform_link_join())
            .outerjoin(User, PlatformLink.user_id == User.id)
            .filter(RunResult.event_id.in_(event_ids))
            .group_by(runner_group_key)
            .order_by(func.count(func.distinct(RunResult.event_id)).desc(), display_name.asc())
            .limit(limit)
            .all()
        )
        runners = [
            {
                "name": name,
                "runs_count": int(runs),
                "best_time_sec": int(best) if best is not None else None,
                "best_time_display": format_finish_time_display(int(best)) if best is not None else None,
            }
            for name, runs, best in runner_rows
        ]
        volunteer_group_key = func.coalesce(PlatformLink.user_id, VolunteerResult.participant_id)
        volunteer_rows = (
            db.query(
                display_name.label("name"),
                func.count(func.distinct(VolunteerResult.event_id)).label("events"),
            )
            .join(Participant, VolunteerResult.participant_id == Participant.id)
            .outerjoin(PlatformLink, _platform_link_join())
            .outerjoin(User, PlatformLink.user_id == User.id)
            .filter(VolunteerResult.event_id.in_(event_ids))
            .group_by(volunteer_group_key)
            .order_by(func.count(func.distinct(VolunteerResult.event_id)).desc(), display_name.asc())
            .limit(limit)
            .all()
        )
        volunteers = [{"name": name, "count": int(events)} for name, events in volunteer_rows]

    return {
        "slug": identity.slug,
        "name": identity.name,
        "runners": runners,
        "volunteers": volunteers,
    }


def _start_point_url(latitude: float | None, longitude: float | None) -> str | None:
    if latitude is None or longitude is None:
        return None
    return f"https://yandex.ru/maps/?pt={longitude},{latitude}&z=16&l=map"


def _first_by_platform_order(
    locations: list[tuple[Location, str]],
    getter: Callable[[Location], object | None],
) -> object | None:
    for location, _code in locations:
        value = getter(location)
        if value is not None:
            return value
    return None


def _last_event_stats(
    db: Session,
    events: Sequence[Any],
    event_ids: list[UUID],
    event_finishers: Callable[[UUID, int | None], int | None],
    avg_finish_time_sec: int | None,
    median_finish_time_sec: int | None,
) -> tuple[dict[str, object] | None, int | None, int | None]:
    """Метрики последнего старта + дельта, на которую он сдвинул средние/медиану.

    Дельта — «после минус до»: считаем avg/медиану заново без последнего
    старта и сравниваем с уже посчитанными по всем событиям значениями.
    Отрицательная дельта — последний старт был быстрее среднего/типичного.
    """
    if not events:
        return None, None, None

    last_event_id, last_event_date, _last_event_number, last_finishers_count, last_platform_code = max(
        events, key=lambda row: row[1]
    )

    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
    row = (
        db.query(
            func.count().label("finishers"),
            func.avg(case((time_ok, RunResult.finish_time_sec))).label("avg_time"),
            func.min(case((time_ok & (gender_expr == "male"), RunResult.finish_time_sec))).label("best_male"),
            func.min(case((time_ok & (gender_expr == "female"), RunResult.finish_time_sec))).label("best_female"),
        )
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .filter(RunResult.event_id == last_event_id)
        .first()
    )
    last_avg_time = int(row.avg_time) if row is not None and row.avg_time is not None else None
    last_best_male = int(row.best_male) if row is not None and row.best_male is not None else None
    last_best_female = int(row.best_female) if row is not None and row.best_female is not None else None

    last_volunteers = (
        db.query(func.count(func.distinct(VolunteerResult.participant_id)))
        .filter(VolunteerResult.event_id == last_event_id, VolunteerResult.participant_id.isnot(None))
        .scalar()
    )

    last_event_payload = {
        "event_date": last_event_date,
        "platform_code": last_platform_code,
        "finishers": event_finishers(last_event_id, last_finishers_count),
        "volunteers": int(last_volunteers) if last_volunteers else None,
        "avg_time_sec": last_avg_time,
        "avg_time_display": format_finish_time_display(last_avg_time) if last_avg_time is not None else None,
        "best_male_time_sec": last_best_male,
        "best_male_time_display": format_finish_time_display(last_best_male) if last_best_male is not None else None,
        "best_female_time_sec": last_best_female,
        "best_female_time_display": (
            format_finish_time_display(last_best_female) if last_best_female is not None else None
        ),
    }

    before_event_ids = [event_id for event_id in event_ids if event_id != last_event_id]
    avg_delta: int | None = None
    median_delta: int | None = None
    if before_event_ids:
        before_avg = (
            db.query(func.avg(RunResult.finish_time_sec))
            .filter(RunResult.event_id.in_(before_event_ids), time_ok)
            .scalar()
        )
        if before_avg is not None and avg_finish_time_sec is not None:
            avg_delta = avg_finish_time_sec - int(before_avg)

        before_median = (
            db.query(func.percentile_cont(0.5).within_group(RunResult.finish_time_sec))
            .filter(RunResult.event_id.in_(before_event_ids), time_ok)
            .scalar()
        )
        if before_median is not None and median_finish_time_sec is not None:
            median_delta = median_finish_time_sec - int(before_median)

    return last_event_payload, avg_delta, median_delta


def build_location_page(db: Session, slug: str, *, use_cache: bool = True) -> dict[str, object] | None:
    cache_key = f"locations:page:v2:{slug.strip().lower()}"
    if use_cache:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_page(db, slug)

    if use_cache and payload is not None:
        _write_json_cache(cache_key, payload, LOCATION_PAGE_CACHE_TTL_SECONDS)
    return payload


def _compute_location_page(db: Session, slug: str) -> dict[str, object] | None:
    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None

    location_ids = [location.id for location, _code in identity.locations]
    event_rows = (
        db.query(
            Event.id,
            Event.event_date,
            Event.event_number,
            Event.finishers_count,
            Platform.code,
        )
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
        )
        .all()
    )
    all_event_ids = [row[0] for row in event_rows]
    kept_event_ids = _dedupe_crosslinked_events(db, all_event_ids)
    events = [row for row in event_rows if row[0] in kept_event_ids]
    event_ids = [row[0] for row in events]

    # Число финишёров события: протокол → events.finishers_count → event_summaries.
    protocol_counts: dict[UUID, int] = {}
    volunteer_counts_total = 0
    unique_participants = 0
    unique_volunteers = 0
    avg_finish_time_sec: int | None = None
    if event_ids:
        protocol_counts = {
            event_id: int(count)
            for event_id, count in db.query(RunResult.event_id, func.count())
            .filter(RunResult.event_id.in_(event_ids))
            .group_by(RunResult.event_id)
            .all()
        }
        volunteer_counts_total = (
            db.query(func.count()).select_from(VolunteerResult).filter(VolunteerResult.event_id.in_(event_ids)).scalar()
            or 0
        )
        unique_participants = (
            db.query(func.count(func.distinct(RunResult.participant_id)))
            .filter(RunResult.event_id.in_(event_ids), RunResult.participant_id.isnot(None))
            .scalar()
            or 0
        )
        unique_volunteers = (
            db.query(func.count(func.distinct(VolunteerResult.participant_id)))
            .filter(VolunteerResult.event_id.in_(event_ids), VolunteerResult.participant_id.isnot(None))
            .scalar()
            or 0
        )
        avg_value = (
            db.query(func.avg(RunResult.finish_time_sec))
            .filter(
                RunResult.event_id.in_(event_ids),
                RunResult.finish_time_sec.isnot(None),
                RunResult.finish_time_sec > 0,
            )
            .scalar()
        )
        avg_finish_time_sec = int(avg_value) if avg_value is not None else None

    summary_counts: dict[UUID, int] = {}
    if location_ids:
        summary_rows = (
            db.query(EventSummary.event_id, EventSummary.finishers_count)
            .filter(
                EventSummary.location_id.in_(location_ids),
                EventSummary.event_id.isnot(None),
                EventSummary.finishers_count.isnot(None),
            )
            .all()
        )
        summary_counts = {event_id: count for event_id, count in summary_rows}

    def event_finishers(event_id: UUID, event_finishers_count: int | None) -> int | None:
        protocol = protocol_counts.get(event_id)
        if protocol:
            return protocol
        if event_finishers_count:
            return event_finishers_count
        return summary_counts.get(event_id)

    finishers_total = 0
    events_with_finishers = 0
    record_finishers = -1
    attendance_record: dict[str, object] | None = None
    for event_id, event_date, event_number, finishers_count, platform_code in events:
        finishers = event_finishers(event_id, finishers_count)
        if finishers is None:
            continue
        finishers_total += finishers
        events_with_finishers += 1
        if finishers > record_finishers:
            record_finishers = finishers
            attendance_record = {
                "finishers": finishers,
                "event_date": event_date,
                "event_number": event_number,
                "platform_code": platform_code,
            }

    course_records = {}
    histogram_rows: list[dict[str, object]] = []
    median_finish_time_sec: int | None = None
    if event_ids:
        course_records = {
            "male": _course_record(db, event_ids, "male"),
            "female": _course_record(db, event_ids, "female"),
        }
        histogram_rows = _histogram_rows(db, event_ids)

        median_value = (
            db.query(func.percentile_cont(0.5).within_group(RunResult.finish_time_sec))
            .filter(
                RunResult.event_id.in_(event_ids),
                RunResult.finish_time_sec.isnot(None),
                RunResult.finish_time_sec > 0,
            )
            .scalar()
        )
        median_finish_time_sec = int(median_value) if median_value is not None else None

    last_event_payload, avg_delta, median_delta = _last_event_stats(
        db,
        events,
        event_ids,
        event_finishers,
        avg_finish_time_sec,
        median_finish_time_sec,
    )

    # Таймлайн платформ (Л1): даты первого/последнего события в каждой системе.
    platform_stats: dict[str, _PlatformStat] = {}
    for _event_id, event_date, _event_number, _finishers_count, platform_code in events:
        stat = platform_stats.get(platform_code)
        if stat is None:
            platform_stats[platform_code] = _PlatformStat(event_date, event_date, 1)
            continue
        stat.events_count += 1
        if event_date < stat.first_event_date:
            stat.first_event_date = event_date
        if event_date > stat.last_event_date:
            stat.last_event_date = event_date

    # Актуальная система: active_platform каталога, иначе система с самым
    # свежим событием (у локаций без каталога active_platform не задан).
    active_platform = (
        normalize_platform_code(identity.catalog.active_platform) if identity.catalog is not None else None
    )
    if active_platform is None and platform_stats:
        active_platform = max(platform_stats.items(), key=lambda item: item[1].last_event_date)[0]

    # Текущая система — всегда последней в таймлайне, остальные — в порядке
    # появления (first_event_date). Иначе более ранняя дата первого старта
    # у активной системы (нередкая при перекрытии платформ на переходе)
    # ставила бы её выше исторических записей.
    ordered_platforms = sorted(
        identity.locations,
        key=lambda item: (
            item[1] == active_platform if active_platform else False,
            platform_stats.get(item[1]) is None,
            platform_stats[item[1]].first_event_date if item[1] in platform_stats else date.max,
            _platform_order_index(item[1]),
        ),
    )
    # Одна запись на платформу: статистика (platform_stats) агрегируется по
    # platform_code, поэтому дубли Location одной системы (напр. две parkrun-строки
    # одной площадки в каталоге) дали бы две одинаковые записи таймлайна.
    platforms_payload: list[dict[str, object]] = []
    seen_platform_codes: set[str] = set()
    for location, platform_code in ordered_platforms:
        if platform_code in seen_platform_codes:
            continue
        seen_platform_codes.add(platform_code)
        stat = platform_stats.get(platform_code)
        platforms_payload.append(
            {
                "platform_code": platform_code,
                "location_name": location.name,
                "external_key": location.external_key,
                "url": location_page_url(platform_code, location.external_key, location.source_url),
                "first_event_date": stat.first_event_date if stat else None,
                "last_event_date": stat.last_event_date if stat else None,
                "events_count": stat.events_count if stat else 0,
                "is_active": platform_code == active_platform if active_platform else None,
            }
        )

    primary_location, primary_code = identity.locations[0]
    catalog_index = identity.catalog_index
    latitude, longitude = catalog_index.coordinates_for(primary_location, primary_code)
    if latitude is None or longitude is None:
        latitude = _first_by_platform_order(identity.locations, lambda loc: loc.latitude)  # type: ignore[assignment]
        longitude = _first_by_platform_order(identity.locations, lambda loc: loc.longitude)  # type: ignore[assignment]

    is_paused = any(catalog_index.is_paused(location, code) for location, code in identity.locations)
    is_cancelled = all(location.is_cancelled for location, _code in identity.locations)

    first_event_date = min((row[1] for row in events), default=None)
    last_event_date = max((row[1] for row in events), default=None)

    return {
        "slug": identity.slug,
        "identity_key": identity.identity_key,
        "name": identity.name,
        "city": _first_by_platform_order(identity.locations, lambda loc: loc.city),
        "region": _first_by_platform_order(identity.locations, lambda loc: loc.region),
        "country": _first_by_platform_order(identity.locations, lambda loc: loc.country),
        "is_paused": is_paused,
        "is_cancelled": is_cancelled,
        "latitude": latitude,
        "longitude": longitude,
        "map_url": _first_by_platform_order(identity.locations, lambda loc: loc.map_url),
        "start_point_url": _start_point_url(latitude, longitude),
        "platforms": platforms_payload,
        "stats": {
            "events_count": len(events),
            "finishers_total": finishers_total,
            "unique_participants": unique_participants,
            "volunteers_total": volunteer_counts_total,
            "unique_volunteers": unique_volunteers,
            "avg_finish_time_sec": avg_finish_time_sec,
            "avg_finish_time_display": (
                format_finish_time_display(avg_finish_time_sec) if avg_finish_time_sec is not None else None
            ),
            "avg_finishers": (round(finishers_total / events_with_finishers) if events_with_finishers else None),
            "attendance_record": attendance_record,
            "course_records": course_records or {"male": None, "female": None},
            "first_event_date": first_event_date,
            "last_event_date": last_event_date,
            "median_finish_time_sec": median_finish_time_sec,
            "median_finish_time_display": (
                format_finish_time_display(median_finish_time_sec) if median_finish_time_sec is not None else None
            ),
            "last_event": last_event_payload,
            "avg_finish_time_delta_sec": avg_delta,
            "median_finish_time_delta_sec": median_delta,
        },
        "histogram": {
            "bin_size_sec": HISTOGRAM_BIN_SEC,
            "rows": histogram_rows,
        },
    }


def build_location_events(db: Session, slug: str, *, use_cache: bool = True) -> dict[str, object] | None:
    cache_key = f"locations:events:v1:{slug.strip().lower()}"
    if use_cache:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_events(db, slug)

    if use_cache and payload is not None:
        _write_json_cache(cache_key, payload, LOCATION_PAGE_CACHE_TTL_SECONDS)
    return payload


def _compute_location_events(db: Session, slug: str) -> dict[str, object] | None:
    """Журнал протоколов локации: все события сквозь все системы, новые сверху."""
    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None

    location_ids = [location.id for location, _code in identity.locations]
    location_by_id = {location.id: location for location, _code in identity.locations}
    event_rows = (
        db.query(Event, Platform.code)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
        )
        .all()
    )
    kept_event_ids = _dedupe_crosslinked_events(db, [event.id for event, _code in event_rows])
    events = [(event, code) for event, code in event_rows if event.id in kept_event_ids]
    event_ids = [event.id for event, _code in events]

    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
    protocol_stats: dict[UUID, dict[str, object]] = {}
    volunteer_counts: dict[UUID, int] = {}
    if event_ids:
        rows = (
            db.query(
                RunResult.event_id,
                func.count().label("finishers"),
                func.min(case((time_ok & (gender_expr == "male"), RunResult.finish_time_sec))).label("best_male"),
                func.min(case((time_ok & (gender_expr == "female"), RunResult.finish_time_sec))).label("best_female"),
                func.avg(case((time_ok, RunResult.finish_time_sec))).label("avg_time"),
                func.sum(case((RunResult.is_first_run.is_(True), 1), else_=0)).label("debutants"),
                func.sum(case((RunResult.is_first_run_at_location.is_(True), 1), else_=0)).label("first_here"),
                func.sum(case((RunResult.is_pr.is_(True), 1), else_=0)).label("prs"),
            )
            .join(Event, RunResult.event_id == Event.id)
            .join(Platform, Event.platform_id == Platform.id)
            .outerjoin(Participant, RunResult.participant_id == Participant.id)
            .filter(RunResult.event_id.in_(event_ids))
            .group_by(RunResult.event_id)
            .all()
        )
        for row in rows:
            protocol_stats[row.event_id] = {
                "finishers": int(row.finishers),
                "best_male": int(row.best_male) if row.best_male is not None else None,
                "best_female": int(row.best_female) if row.best_female is not None else None,
                "avg_time": int(row.avg_time) if row.avg_time is not None else None,
                "debutants": int(row.debutants or 0),
                "first_here": int(row.first_here or 0),
                "prs": int(row.prs or 0),
            }
        volunteer_counts = {
            event_id: int(count)
            for event_id, count in db.query(
                VolunteerResult.event_id, func.count(func.distinct(VolunteerResult.participant_id))
            )
            .filter(VolunteerResult.event_id.in_(event_ids))
            .group_by(VolunteerResult.event_id)
            .all()
        }

    summaries: dict[UUID, EventSummary] = {}
    if location_ids:
        for summary_row in (
            db.query(EventSummary)
            .filter(EventSummary.location_id.in_(location_ids), EventSummary.event_id.isnot(None))
            .all()
        ):
            if summary_row.event_id is not None:
                summaries[summary_row.event_id] = summary_row

    def fmt(value: int | None) -> str | None:
        return format_finish_time_display(value) if value is not None else None

    items: list[dict[str, object]] = []
    for event, platform_code in events:
        stats = protocol_stats.get(event.id)
        summary = summaries.get(event.id)
        has_protocol = stats is not None
        finishers = (
            stats["finishers"]
            if stats is not None
            else (event.finishers_count or (summary.finishers_count if summary else None))
        )
        volunteers = volunteer_counts.get(event.id) or (summary.volunteers_count if summary else None)
        best_male = stats["best_male"] if stats else (summary.best_male_time_sec if summary else None)
        best_female = stats["best_female"] if stats else (summary.best_female_time_sec if summary else None)
        avg_time = stats["avg_time"] if stats else (summary.avg_time_sec if summary else None)
        items.append(
            {
                "event_date": event.event_date,
                "platform_code": platform_code,
                "event_number": event.event_number,
                "finishers": finishers,
                "volunteers": volunteers,
                "best_male_time_sec": best_male,
                "best_male_time_display": fmt(best_male),  # type: ignore[arg-type]
                "best_female_time_sec": best_female,
                "best_female_time_display": fmt(best_female),  # type: ignore[arg-type]
                "avg_time_sec": avg_time,
                "avg_time_display": fmt(avg_time),  # type: ignore[arg-type]
                "debutants": stats["debutants"] if stats else None,
                "first_at_location": stats["first_here"] if stats else None,
                "prs": stats["prs"] if stats else None,
                "has_protocol": has_protocol,
                "protocol_url": resolve_activity_url(
                    platform_code=platform_code,
                    event_date=event.event_date,
                    event_number=event.event_number,
                    event_source_url=event.source_url,
                    location_external_key=location_by_id[event.location_id].external_key,
                    summary_source_url=summary.source_url if summary else None,
                ),
            }
        )

    # Прогрессия рекорда посещаемости и рекордов трассы М/Ж: маркер у КАЖДОГО
    # старта, побившего рекорд на свой момент (новый рекорд не снимает маркер
    # со старых). "Глобальный" рекорд — сквозь все платформы идентичности разом
    # (единая хронология); "внутрисистемный" — свой рекорд для каждой
    # платформы отдельно (глобальный всегда является и внутрисистемным на
    # свой момент, т.к. считается по той же убывающей последовательности).
    # Заодно нумеруем старты сквозным номером — какой это по счёту сбор
    # локации за всю историю, вне зависимости от платформы.
    items.sort(key=lambda item: cast(date, item["event_date"]))
    running_max_finishers: int | None = None
    running_best_male: int | None = None
    running_best_female: int | None = None
    running_max_finishers_by_platform: dict[str, int] = {}
    running_best_male_by_platform: dict[str, int] = {}
    running_best_female_by_platform: dict[str, int] = {}
    for overall_number, item in enumerate(items, start=1):
        platform_code = cast(str, item["platform_code"])

        finishers = cast("int | None", item["finishers"])
        is_record = finishers is not None and (running_max_finishers is None or finishers > running_max_finishers)
        item["is_attendance_record"] = is_record
        if is_record:
            running_max_finishers = finishers
        platform_max = running_max_finishers_by_platform.get(platform_code)
        is_platform_record = finishers is not None and (platform_max is None or finishers > platform_max)
        item["is_platform_attendance_record"] = is_platform_record
        if is_platform_record:
            running_max_finishers_by_platform[platform_code] = cast(int, finishers)

        best_male = cast("int | None", item["best_male_time_sec"])
        is_male_record = best_male is not None and (running_best_male is None or best_male < running_best_male)
        item["is_course_record_male"] = is_male_record
        if is_male_record:
            running_best_male = best_male
        platform_best_male = running_best_male_by_platform.get(platform_code)
        is_platform_male_record = best_male is not None and (
            platform_best_male is None or best_male < platform_best_male
        )
        item["is_platform_course_record_male"] = is_platform_male_record
        if is_platform_male_record:
            running_best_male_by_platform[platform_code] = cast(int, best_male)

        best_female = cast("int | None", item["best_female_time_sec"])
        is_female_record = best_female is not None and (
            running_best_female is None or best_female < running_best_female
        )
        item["is_course_record_female"] = is_female_record
        if is_female_record:
            running_best_female = best_female
        platform_best_female = running_best_female_by_platform.get(platform_code)
        is_platform_female_record = best_female is not None and (
            platform_best_female is None or best_female < platform_best_female
        )
        item["is_platform_course_record_female"] = is_platform_female_record
        if is_platform_female_record:
            running_best_female_by_platform[platform_code] = cast(int, best_female)

        item["overall_number"] = overall_number
    items.sort(key=lambda item: cast(date, item["event_date"]), reverse=True)

    return {
        "slug": identity.slug,
        "name": identity.name,
        "total": len(items),
        "items": items,
    }


@dataclass
class _IndexIdentityStat:
    events_count: int = 0
    finishers_total: int = 0
    first_event_date: date | None = None
    last_event_date: date | None = None


def _bulk_identity_stats(
    db: Session,
    location_id_to_identity: dict[UUID, str],
) -> dict[str, _IndexIdentityStat]:
    """Сводные цифры по всем идентичностям сразу — без N+1 запросов на локацию."""
    location_ids = list(location_id_to_identity.keys())
    if not location_ids:
        return {}

    event_rows = (
        db.query(Event.id, Event.location_id, Event.event_date, Event.finishers_count)
        .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
        .all()
    )
    all_event_ids = {row[0] for row in event_rows}

    # Кросслинки всегда связывают события ОДНОЙ идентичности (тот же
    # физический старт, записанный в двух системах) — дедуп безопасен глобально,
    # без группировки по identity. JOIN по location_id (короткий список),
    # а не IN() по event_id (в проде — десятки тысяч id, огромный список
    # параметров убивает время запроса через SSH-туннель на dev).
    excluded_secondary: set[UUID] = set()
    if all_event_ids:
        crosslink_rows = (
            db.query(EventCrosslink.primary_event_id, EventCrosslink.secondary_event_id)
            .join(Event, EventCrosslink.secondary_event_id == Event.id)
            .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
            .all()
        )
        excluded_secondary = {secondary for primary, secondary in crosslink_rows if primary in all_event_ids}

    # events.finishers_count не заполняется у s95 (только у 5verst) — для
    # верных цифр нужен COUNT по run_results, events.finishers_count только
    # как fallback для событий без протокола (parkrun-эпоха).
    protocol_counts: dict[UUID, int] = {
        event_id: int(count)
        for event_id, count in db.query(RunResult.event_id, func.count())
        .join(Event, RunResult.event_id == Event.id)
        .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
        .group_by(RunResult.event_id)
        .all()
    }

    stats: dict[str, _IndexIdentityStat] = {}
    for event_id, location_id, event_date, finishers_count in event_rows:
        if event_id in excluded_secondary:
            continue
        identity_key = location_id_to_identity[location_id]
        stat = stats.setdefault(identity_key, _IndexIdentityStat())
        stat.events_count += 1
        finishers = protocol_counts.get(event_id) or finishers_count
        if finishers:
            stat.finishers_total += finishers
        if stat.first_event_date is None or event_date < stat.first_event_date:
            stat.first_event_date = event_date
        if stat.last_event_date is None or event_date > stat.last_event_date:
            stat.last_event_date = event_date

    return stats


def _read_json_cache(key: str) -> dict[str, object] | None:
    try:
        raw = get_redis_client().get(key)
    except redis.RedisError:
        return None
    if not isinstance(raw, str):
        return None
    try:
        return cast(dict[str, object], json.loads(raw))
    except (TypeError, ValueError):
        return None


def _write_json_cache(key: str, payload: dict[str, object], ttl_seconds: int) -> None:
    try:
        get_redis_client().setex(key, ttl_seconds, json.dumps(payload, default=str))
    except redis.RedisError:
        pass


def _read_locations_index_cache() -> dict[str, object] | None:
    return _read_json_cache(LOCATIONS_INDEX_CACHE_KEY)


def _write_locations_index_cache(payload: dict[str, object]) -> None:
    _write_json_cache(LOCATIONS_INDEX_CACHE_KEY, payload, LOCATIONS_INDEX_CACHE_TTL_SECONDS)


def invalidate_locations_index_cache() -> None:
    try:
        get_redis_client().delete(LOCATIONS_INDEX_CACHE_KEY)
    except redis.RedisError:
        pass


def invalidate_location_page_cache(slug: str) -> None:
    """Ручной сброс кэша страницы/журнала/рейтингов одной локации (TTL сам разрулит остальное)."""
    normalized = slug.strip().lower()
    try:
        client = get_redis_client()
        client.delete(
            f"locations:page:v2:{normalized}",
            f"locations:events:v1:{normalized}",
            f"locations:leaders:v1:{normalized}",
        )
    except redis.RedisError:
        pass


def build_locations_index(db: Session, *, use_cache: bool = True) -> dict[str, object]:
    """Публичный каталог локаций — с TTL-кэшем в Redis (см. LOCATIONS_INDEX_CACHE_TTL_SECONDS).

    Redis недоступен → тихо считаем без кэша (кэш — оптимизация, не зависимость).
    """
    if use_cache:
        cached = _read_locations_index_cache()
        if cached is not None:
            return cached

    payload = _compute_locations_index(db)

    if use_cache:
        _write_locations_index_cache(payload)
    return payload


def _compute_locations_index(db: Session) -> dict[str, object]:
    """Тяжёлая агрегация каталога локаций: одна строка на каноническую идентичность."""
    catalog_index = LocationCatalogIndex(db)

    # Строки таблицы — только «официальные» локации активных систем
    # (five_verst/s95/runpark): у parkrun-локаций почти всегда есть текущий
    # преемник в каталоге, отдельной строки без него не показываем.
    display_rows = (
        db.query(Location, Platform.code)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(Platform.code.in_(("five_verst", "s95", "runpark")), Location.is_official_map.is_(True))
        .all()
    )
    identity_locations: dict[str, list[tuple[Location, str]]] = {}
    for location, platform_code in display_rows:
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        identity_locations.setdefault(identity_key, []).append((location, platform_code))

    # Цифры (стартов, финишей, первого старта) — СКВОЗНЫЕ по всей истории
    # идентичности, включая parkrun-эпоху: иначе «Первый старт» теряет годы
    # до перехода на текущую систему, а «Стартов»/«Финишей» занижены.
    all_rows = db.query(Location, Platform.code).join(Platform, Location.platform_id == Platform.id).all()
    location_id_to_identity: dict[UUID, str] = {}
    for location, platform_code in all_rows:
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        if identity_key in identity_locations:
            location_id_to_identity[location.id] = identity_key

    identity_stats = _bulk_identity_stats(db, location_id_to_identity)

    items: list[dict[str, object]] = []
    for identity_key, members in identity_locations.items():
        catalog = catalog_index.get_for_identity_key(identity_key)
        ordered = _sort_identity_locations(catalog, members)
        primary_location, primary_code = ordered[0]
        stat = identity_stats.get(identity_key)
        items.append(
            {
                "slug": primary_location.external_key.strip().lower(),
                "identity_key": identity_key,
                "name": _identity_display_name(catalog, ordered),
                "city": _first_by_platform_order(ordered, lambda loc: loc.city),
                "region": _first_by_platform_order(ordered, lambda loc: loc.region),
                "country": _first_by_platform_order(ordered, lambda loc: loc.country),
                "platform_codes": sorted({code for _loc, code in ordered}, key=_platform_order_index),
                "is_paused": any(catalog_index.is_paused(location, code) for location, code in ordered),
                "is_cancelled": all(location.is_cancelled for location, _code in ordered),
                "events_count": stat.events_count if stat else 0,
                "finishers_total": stat.finishers_total if stat else 0,
                "first_event_date": stat.first_event_date if stat else None,
                "last_event_date": stat.last_event_date if stat else None,
            }
        )

    items.sort(key=lambda item: str(item["name"]).lower())
    return {"items": items, "total": len(items)}
