"""Рекорды локаций, которые участник держит сейчас или держал раньше.

Рекорд локации — лучшее время среди всех финишёров площадки (в рамках пола).
Рекорд «на момент забега» восстанавливается из истории протоколов: пробежка
была рекордом, если оказалась строго быстрее всех предыдущих результатов
площадки. Такая прогрессия (префиксные минимумы по хронологии) даёт сразу всё:
текущего держателя (последняя строка), моменты установки рекорда (вехи) и
утерянные рекорды с датой и автором перебившего результата.

Уровни рекорда. Для площадок, живших в нескольких системах (parkrun → 5 вёрст,
runpark + s95 и т.п., связь — location_catalog), рекорд двухуровневый:
«глобальный» (сквозь все системы идентичности) и «по системе» (внутри одной).
Глобальный рекорд по построению является и рекордом своей системы (глобальная
прогрессия включает все её результаты), поэтому такие пары схлопываются в одну
запись уровня «глобальный». Для монолокаций уровень один и не показывается.

Рекорды по возрастным группам считаются только по 5 вёрст — единственной
системе с возрастной категорией в протоколе (см. комментарий к
FIVE_VERST_PLATFORM_CODE в location_page_service) — и потому всегда
одноуровневые.

Как это считается. Прогрессия — свойство площадки, а не пользователя: у всех,
кто там бегал, она одна и та же. Поэтому расчёт разложен на три фазы:
перечислить нужные прогрессии, разом их достать (общий кэш + один батч-запрос
на всё, чего в кэше нет), и только потом разложить на серии рекордов конкретного
участника. Запрос на площадку в цикле давал 25 с на профиль «туриста» и ронял
загрузку личного кабинета по таймауту.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, cast
from uuid import UUID

import redis
from sqlalchemy import Integer, bindparam, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import (
    Event,
    Location,
    LocationCatalogLink,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_page_service import (
    FIVE_VERST_PLATFORM_CODE,
    _age_group_match_clauses,
    _dedupe_crosslinked_events,
    _gender_expression,
    _identity_display_name,
    _participant_display_names,
    _platform_link_join,
    _protocol_age_category_gender,
    _sort_identity_locations,
    normalize_age_group,
)
from app.time_format import format_finish_time_display

GLOBAL_LEVEL = "global"

# Тот же TTL, что у страницы локации: рекорды меняются только при доливке
# протоколов. После user-sync пересчёт дашборда перезаписывает кэш свежим
# значением (force_refresh), TTL страхует от чужих результатов между синками.
LOCATION_RECORDS_CACHE_TTL_SECONDS = 3 * 60 * 60

# Прогрессия рекордов площадки одинакова для всех — она про саму площадку, а не
# про пользователя. Поэтому она лежит в общем кэше: посчитал первый зашедший,
# остальные читают. Ключ включает отпечаток данных локаций (см.
# _location_fingerprints), так что доливка протокола меняет ключ и стухшую
# запись никто не увидит — явная инвалидация не нужна, TTL просто подчищает.
PROGRESSION_CACHE_PREFIX = "locrec:prog:v1"
PROGRESSION_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def user_location_records_cache_key(user_id: UUID) -> str:
    return f"dashboard:location-records:v1:{user_id}"


@dataclass
class _ProgressionRow:
    """Результат, ставший рекордом площадки на момент своего забега."""

    run_result_id: UUID
    participant_id: UUID | None
    finish_time_sec: int
    event_date: date
    event_id: UUID
    platform_code: str


def _prefix_minima(db: Session, base: Any, *, partitioned: bool = False) -> list[Any]:
    """Префиксные минимумы: окно бежит по хронологии, наружу выходят только
    строки, побившие всё, что было до них. Внутри одного дня первым идёт самое
    быстрое время — остальные результаты дня рекорда не получают. Фильтр по
    оконной функции в WHERE запрещён, отсюда двухэтажный subquery.

    С partitioned=True в base есть колонка scope_idx и окно бежит внутри
    каждого скоупа отдельно — так прогрессии десятков площадок считаются одним
    запросом вместо запроса на площадку (см. _batch_course_progressions)."""
    subq = base.subquery()
    over: dict[str, Any] = {
        "order_by": (
            subq.c.event_date.asc(),
            subq.c.finish_time_sec.asc(),
            subq.c.position.asc().nulls_last(),
            subq.c.run_result_id.asc(),
        ),
        "rows": (None, -1),
    }
    if partitioned:
        over["partition_by"] = subq.c.scope_idx
    prev_min = func.min(subq.c.finish_time_sec).over(**over).label("prev_min")
    ranked = db.query(subq, prev_min).subquery()
    order = [ranked.c.event_date.asc(), ranked.c.finish_time_sec.asc()]
    if partitioned:
        order.insert(0, ranked.c.scope_idx)
    return (
        db.query(ranked)
        .filter(or_(ranked.c.prev_min.is_(None), ranked.c.finish_time_sec < ranked.c.prev_min))
        .order_by(*order)
        .all()
    )


def _scope_events(event_id_lists: list[list[UUID]]) -> Any:
    """Развёрнутая таблица (scope_idx, event_id) из двух параллельных массивов.

    Списки скоупов сильно пересекаются (глобальный = объединение системных),
    поэтому пары передаются как есть — так одна строка результата попадает во
    все свои скоупы, и PARTITION BY считает их независимо."""
    idx_arr: list[int] = []
    evt_arr: list[UUID] = []
    for scope_idx, event_ids in enumerate(event_id_lists):
        for event_id in event_ids:
            idx_arr.append(scope_idx)
            evt_arr.append(event_id)
    return select(
        func.unnest(bindparam("scope_idx_arr", value=idx_arr, type_=ARRAY(Integer))).label(
            "scope_idx"
        ),
        func.unnest(
            bindparam("scope_event_id_arr", value=evt_arr, type_=ARRAY(PG_UUID(as_uuid=True)))
        ).label("event_id"),
    ).subquery("scope_events")


def _to_progression_row(row: Any) -> _ProgressionRow:
    return _ProgressionRow(
        run_result_id=row.run_result_id,
        participant_id=row.participant_id,
        finish_time_sec=int(row.finish_time_sec),
        event_date=row.event_date,
        event_id=row.event_id,
        platform_code=row.platform_code,
    )


def _batch_course_progressions(
    db: Session, event_id_lists: list[list[UUID]], gender: str
) -> list[list[_ProgressionRow]]:
    """Прогрессии рекорда трассы сразу для набора скоупов — одним запросом."""
    out: list[list[_ProgressionRow]] = [[] for _ in event_id_lists]
    if not any(event_id_lists):
        return out
    scope_events = _scope_events(event_id_lists)
    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    base = (
        db.query(
            scope_events.c.scope_idx.label("scope_idx"),
            RunResult.id.label("run_result_id"),
            RunResult.participant_id.label("participant_id"),
            RunResult.finish_time_sec.label("finish_time_sec"),
            RunResult.position.label("position"),
            Event.event_date.label("event_date"),
            Event.id.label("event_id"),
            Platform.code.label("platform_code"),
        )
        .select_from(scope_events)
        .join(RunResult, RunResult.event_id == scope_events.c.event_id)
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .filter(
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
            gender_expr == gender,
        )
    )
    for row in _prefix_minima(db, base, partitioned=True):
        out[row.scope_idx].append(_to_progression_row(row))
    return out


def _batch_age_group_progressions(
    db: Session, requests: list[tuple[list[UUID], str]], gender: str
) -> list[list[_ProgressionRow]]:
    """Прогрессии рекорда возрастной группы (только 5 вёрст), запрос на группу.

    LIKE-паттерны зеркалят normalize_age_group приблизительно, поэтому пол
    фильтруется отдельно, а точная принадлежность группе перепроверяется
    нормализацией в Python; после отсева прогрессия пересобирается заново —
    чужая категория могла «занять» рекорд, которого в этой группе не было.

    Батчим по возрастной группе: LIKE-условие у скоупов одной группы общее, а
    разных — разное, поэтому в один запрос их не свести без риска, что строка
    подойдёт под чужой паттерн.
    """
    out: list[list[_ProgressionRow]] = [[] for _ in requests]
    by_group: dict[str, list[int]] = {}
    for index, (event_ids, age_group) in enumerate(requests):
        if event_ids:
            by_group.setdefault(age_group, []).append(index)

    for age_group, indexes in by_group.items():
        scope_events = _scope_events([requests[index][0] for index in indexes])
        base = (
            db.query(
                scope_events.c.scope_idx.label("scope_idx"),
                RunResult.id.label("run_result_id"),
                RunResult.participant_id.label("participant_id"),
                RunResult.finish_time_sec.label("finish_time_sec"),
                RunResult.position.label("position"),
                RunResult.age_category.label("age_category"),
                Event.event_date.label("event_date"),
                Event.id.label("event_id"),
                Platform.code.label("platform_code"),
            )
            .select_from(scope_events)
            .join(RunResult, RunResult.event_id == scope_events.c.event_id)
            .join(Event, RunResult.event_id == Event.id)
            .join(Platform, Event.platform_id == Platform.id)
            .filter(
                Platform.code == FIVE_VERST_PLATFORM_CODE,
                RunResult.finish_time_sec.isnot(None),
                RunResult.finish_time_sec > 0,
                RunResult.age_category.isnot(None),
                RunResult.age_category != "",
                _protocol_age_category_gender() == gender,
                or_(*_age_group_match_clauses([age_group])),
            )
        )
        running_min: dict[int, int] = {}
        for row in _prefix_minima(db, base, partitioned=True):
            if normalize_age_group(row.age_category) != age_group:
                continue
            finish = int(row.finish_time_sec)
            previous = running_min.get(row.scope_idx)
            if previous is not None and finish >= previous:
                continue
            running_min[row.scope_idx] = finish
            out[indexes[row.scope_idx]].append(_to_progression_row(row))
    return out


@dataclass
class _UserStreak:
    """Непрерывная серия рекордных строк участника в прогрессии.

    Улучшения собственного рекорда схлопываются в одну запись витрины
    (показываем итоговое время серии), но каждая строка серии — отдельная веха.
    """

    rows: list[_ProgressionRow]
    next_row: _ProgressionRow | None  # кто перебил (None — рекорд держится)

    @property
    def last(self) -> _ProgressionRow:
        return self.rows[-1]


def _user_streaks(
    progression: list[_ProgressionRow], user_participant_ids: set[UUID]
) -> list[_UserStreak]:
    streaks: list[_UserStreak] = []
    current: list[_ProgressionRow] = []
    for row in progression:
        if row.participant_id in user_participant_ids:
            current.append(row)
            continue
        if current:
            streaks.append(_UserStreak(rows=current, next_row=row))
            current = []
    if current:
        streaks.append(_UserStreak(rows=current, next_row=None))
    return streaks


@dataclass
class _IdentityScopes:
    identity_key: str
    name: str
    slug: str
    city: str | None
    multi_system: bool
    location_ids: set[UUID]
    all_event_ids: list[UUID]  # с дедупом кросслинков — для глобальной прогрессии
    events_by_platform: dict[str, list[UUID]]


def _load_identity_scopes(db: Session, visited_location_ids: set[UUID]) -> list[_IdentityScopes]:
    """Сгруппировать посещённые локации в идентичности каталога и подтянуть
    «соседние» строки других систем: непосещённая parkrun-эра тоже участвует
    в глобальной прогрессии."""
    if not visited_location_ids:
        return []
    catalog_index = LocationCatalogIndex(db)

    def load_locations(ids: set[UUID]) -> list[tuple[Location, str]]:
        return cast(
            list[tuple[Location, str]],
            db.query(Location, Platform.code)
            .join(Platform, Location.platform_id == Platform.id)
            .filter(Location.id.in_(ids))
            .all(),
        )

    visited = load_locations(visited_location_ids)
    catalog_ids: set[UUID] = set()
    for location, code in visited:
        catalog = catalog_index.get_for_location(location, code)
        if catalog is not None:
            catalog_ids.add(catalog.id)

    sibling_ids: set[UUID] = set()
    if catalog_ids:
        rows = (
            db.query(LocationCatalogLink.location_id)
            .filter(
                LocationCatalogLink.catalog_id.in_(catalog_ids),
                LocationCatalogLink.location_id.isnot(None),
            )
            .all()
        )
        sibling_ids = {row[0] for row in rows} - visited_location_ids

    members = visited + (load_locations(sibling_ids) if sibling_ids else [])

    identity_locations: dict[str, list[tuple[Location, str]]] = {}
    for location, code in members:
        key = catalog_index.canonical_identity_key(location, code)
        identity_locations.setdefault(key, []).append((location, code))

    event_rows = (
        db.query(Event.id, Event.location_id)
        .filter(
            Event.location_id.in_([location.id for location, _code in members]),
            Event.is_test_event.is_(False),
        )
        .all()
    )
    events_by_location: dict[UUID, list[UUID]] = {}
    for event_id, location_id in event_rows:
        events_by_location.setdefault(location_id, []).append(event_id)

    result: list[_IdentityScopes] = []
    for key, locations in identity_locations.items():
        catalog = catalog_index.get_for_identity_key(key)
        ordered = _sort_identity_locations(catalog, locations)
        events_by_platform: dict[str, list[UUID]] = {}
        for location, code in ordered:
            ids = events_by_location.get(location.id, [])
            if ids:
                events_by_platform.setdefault(code, []).extend(ids)
        if not events_by_platform:
            continue
        all_ids = [event_id for ids in events_by_platform.values() for event_id in ids]
        kept = _dedupe_crosslinked_events(db, all_ids)
        result.append(
            _IdentityScopes(
                identity_key=key,
                name=_identity_display_name(catalog, ordered, catalog_index),
                slug=ordered[0][0].external_key.strip().lower(),
                city=next((loc.city for loc, _code in ordered if loc.city), None),
                multi_system=len(events_by_platform) > 1,
                location_ids={location.id for location, _code in ordered},
                all_event_ids=[event_id for event_id in all_ids if event_id in kept],
                events_by_platform=events_by_platform,
            )
        )
    return result


def _user_participants(db: Session, user_id: UUID) -> list[tuple[Participant, str]]:
    rows = (
        db.query(Participant, Platform.code)
        .select_from(PlatformLink)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .join(Participant, _platform_link_join())
        .filter(PlatformLink.user_id == user_id)
        .all()
    )
    return cast(list[tuple[Participant, str]], rows)


def _entry(
    identity: _IdentityScopes,
    streak: _UserStreak,
    *,
    level: str | None,
    age_group: str | None,
    beaten_names: dict[UUID, tuple[str | None, str | None]],
) -> dict[str, object]:
    last = streak.last
    beaten = streak.next_row
    beaten_by: str | None = None
    if beaten is not None and beaten.participant_id is not None:
        beaten_by = beaten_names.get(beaten.participant_id, (None, None))[0]
    return {
        "location_name": identity.name,
        "location_slug": identity.slug,
        "location_city": identity.city,
        "level": level,
        "platform_code": last.platform_code,
        "age_group": age_group,
        "finish_time_sec": last.finish_time_sec,
        "finish_time_display": format_finish_time_display(last.finish_time_sec),
        "event_date": last.event_date.isoformat(),
        "run_result_id": str(last.run_result_id),
        "is_current": beaten is None,
        "beaten_date": beaten.event_date.isoformat() if beaten is not None else None,
        "beaten_by": beaten_by,
        "beaten_time_sec": beaten.finish_time_sec if beaten is not None else None,
        "beaten_time_display": (
            format_finish_time_display(beaten.finish_time_sec) if beaten is not None else None
        ),
    }


def _milestone_row(
    identity: _IdentityScopes,
    row: _ProgressionRow,
    *,
    kind: str,
    record_scope: str | None,
    age_group: str | None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "run_result_id": str(row.run_result_id),
        "event_id": str(row.event_id),
        "event_date": row.event_date.isoformat(),
        "platform_code": row.platform_code,
        "location_name": identity.name,
        "location_city": identity.city,
        "finish_time_sec": row.finish_time_sec,
        "finish_time_display": format_finish_time_display(row.finish_time_sec),
        "record_scope": record_scope,
        "age_group": age_group,
    }


def _streak_key(identity: _IdentityScopes, streak: _UserStreak) -> tuple[str, UUID, UUID | None]:
    return (
        identity.identity_key,
        streak.last.run_result_id,
        streak.next_row.run_result_id if streak.next_row is not None else None,
    )


def _sort_entries(entries: list[dict[str, object]]) -> None:
    # Сначала действующие (свежеустановленные выше), затем утерянные —
    # свежеперебитые выше, чтобы «что я потерял» находилось с первого взгляда.
    # Два стабильных прохода: дата по убыванию, затем группировка по статусу.
    entries.sort(
        key=lambda entry: str(entry["event_date"] if entry["is_current"] else entry["beaten_date"]),
        reverse=True,
    )
    entries.sort(key=lambda entry: 0 if entry["is_current"] else 1)


def _location_fingerprints(db: Session, location_ids: set[UUID]) -> dict[UUID, str]:
    """Отпечаток данных локации для ключа кэша прогрессий.

    Число протоколов, дата последнего и время последней правки: доливка нового
    протокола или пересбор старого меняют отпечаток, а с ним и ключ кэша.
    Запрос идёт только по events (без run_results) — на 176 локациях это ~50 мс.
    """
    if not location_ids:
        return {}
    rows = (
        db.query(
            Event.location_id,
            func.count(Event.id),
            func.max(Event.event_date),
            func.max(Event.updated_at),
        )
        .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
        .group_by(Event.location_id)
        .all()
    )
    return {row[0]: f"{row[1]}/{row[2]}/{row[3]}" for row in rows}


def _scope_cache_key(
    identity: _IdentityScopes,
    *,
    kind: str,
    variant: str | None,
    gender: str,
    fingerprints: dict[UUID, str],
) -> str:
    parts = [kind, identity.identity_key, variant or "-", gender]
    parts.extend(
        f"{location_id}={fingerprints.get(location_id, '-')}"
        for location_id in sorted(identity.location_ids, key=str)
    )
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return f"{PROGRESSION_CACHE_PREFIX}:{digest}"


def _dump_progression(rows: list[_ProgressionRow]) -> str:
    return json.dumps(
        [
            [
                str(row.run_result_id),
                str(row.participant_id) if row.participant_id is not None else None,
                row.finish_time_sec,
                row.event_date.isoformat(),
                str(row.event_id),
                row.platform_code,
            ]
            for row in rows
        ]
    )


def _load_progression(raw: str) -> list[_ProgressionRow] | None:
    try:
        items = json.loads(raw)
        return [
            _ProgressionRow(
                run_result_id=UUID(item[0]),
                participant_id=UUID(item[1]) if item[1] is not None else None,
                finish_time_sec=int(item[2]),
                event_date=date.fromisoformat(item[3]),
                event_id=UUID(item[4]),
                platform_code=item[5],
            )
            for item in items
        ]
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def _resolve_progressions(
    keys: list[str], compute: Callable[[list[int]], list[list[_ProgressionRow]]]
) -> list[list[_ProgressionRow]]:
    """Отдать прогрессии по ключам: что есть в общем кэше — оттуда, остальное
    посчитать одним батчем через compute(indexes) и положить в кэш."""
    positions: dict[str, list[int]] = {}
    for index, key in enumerate(keys):
        positions.setdefault(key, []).append(index)
    unique_keys = list(positions)

    cached: list[str | None] = [None] * len(unique_keys)
    if unique_keys:
        try:
            raw_values = cast(list[Any], get_redis_client().mget(unique_keys))
            cached = [value if isinstance(value, str) else None for value in raw_values]
        except redis.RedisError:
            pass

    resolved: list[list[_ProgressionRow] | None] = [None] * len(keys)
    missing: list[str] = []
    for key, raw in zip(unique_keys, cached, strict=True):
        rows = _load_progression(raw) if raw is not None else None
        if rows is None:
            missing.append(key)
            continue
        for index in positions[key]:
            resolved[index] = rows

    if missing:
        computed = compute([positions[key][0] for key in missing])
        for key, rows in zip(missing, computed, strict=True):
            for index in positions[key]:
                resolved[index] = rows
        try:
            pipe = get_redis_client().pipeline()
            for key, rows in zip(missing, computed, strict=True):
                pipe.setex(key, PROGRESSION_CACHE_TTL_SECONDS, _dump_progression(rows))
            pipe.execute()
        except redis.RedisError:
            pass

    return [[] if rows is None else rows for rows in resolved]


def compute_user_location_records(db: Session, user_id: UUID) -> dict[str, object]:
    """Полная картина рекордов пользователя: действующие, утерянные, вехи."""
    participants = _user_participants(db, user_id)
    participant_ids = {participant.id for participant, _code in participants}
    gender = next(
        (participant.gender for participant, _code in participants if participant.gender), None
    )

    payload: dict[str, object] = {
        "course": {"current_count": 0, "lost_count": 0, "entries": []},
        "age_group": {"current_count": 0, "lost_count": 0, "entries": []},
        "milestones": [],
    }
    if not participant_ids or gender not in ("male", "female"):
        return payload

    # Где пользователь финишировал: локации и его возрастные группы на них.
    visited_rows = (
        db.query(Event.location_id, Platform.code, RunResult.age_category)
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            RunResult.participant_id.in_(participant_ids),
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
            Event.is_test_event.is_(False),
        )
        .distinct()
        .all()
    )
    if not visited_rows:
        return payload
    visited_location_ids = {row[0] for row in visited_rows}
    platforms_by_location: dict[UUID, set[str]] = {}
    user_groups_by_location: dict[UUID, set[str]] = {}
    for location_id, platform_code, age_category in visited_rows:
        platforms_by_location.setdefault(location_id, set()).add(platform_code)
        if platform_code == FIVE_VERST_PLATFORM_CODE:
            group = normalize_age_group(age_category)
            if group is not None:
                user_groups_by_location.setdefault(location_id, set()).add(group)

    identities = _load_identity_scopes(db, visited_location_ids)

    # Фаза 1: перечислить нужные прогрессии. Каждая из них — свойство площадки,
    # а не пользователя, поэтому дальше они разрешаются через общий кэш и один
    # батч-запрос на всё недостающее.
    course_specs: list[tuple[_IdentityScopes, str | None, list[UUID]]] = []
    age_specs: list[tuple[_IdentityScopes, str, list[UUID]]] = []
    for identity in identities:
        user_platforms = {
            code
            for location_id in identity.location_ids & visited_location_ids
            for code in platforms_by_location.get(location_id, set())
        }
        if not user_platforms:
            continue

        if identity.multi_system:
            course_specs.append((identity, GLOBAL_LEVEL, identity.all_event_ids))
            for code in sorted(user_platforms):
                course_specs.append((identity, code, identity.events_by_platform[code]))
        else:
            course_specs.append((identity, None, identity.all_event_ids))

        five_verst_events = identity.events_by_platform.get(FIVE_VERST_PLATFORM_CODE)
        if five_verst_events:
            identity_groups: set[str] = set()
            for location_id in identity.location_ids:
                identity_groups |= user_groups_by_location.get(location_id, set())
            for age_group in sorted(identity_groups):
                age_specs.append((identity, age_group, five_verst_events))

    # Фаза 2: разрешить прогрессии — кэш плюс батч на промахи.
    fingerprints = _location_fingerprints(
        db, {location_id for identity in identities for location_id in identity.location_ids}
    )
    course_progressions = _resolve_progressions(
        [
            _scope_cache_key(
                identity, kind="course", variant=level, gender=gender, fingerprints=fingerprints
            )
            for identity, level, _event_ids in course_specs
        ],
        lambda indexes: _batch_course_progressions(
            db, [course_specs[index][2] for index in indexes], gender
        ),
    )
    age_progressions = _resolve_progressions(
        [
            _scope_cache_key(
                identity, kind="age", variant=age_group, gender=gender, fingerprints=fingerprints
            )
            for identity, age_group, _event_ids in age_specs
        ],
        lambda indexes: _batch_age_group_progressions(
            db, [(age_specs[index][2], age_specs[index][1]) for index in indexes], gender
        ),
    )

    # Фаза 3: разложить прогрессии на серии рекордов пользователя.
    raw_course: list[tuple[_IdentityScopes, str | None, _UserStreak]] = []
    raw_age: list[tuple[_IdentityScopes, str, _UserStreak]] = []
    global_user_runs_by_identity: dict[str, set[UUID]] = {}
    beaten_participant_ids: set[UUID] = set()

    def collect(streak: _UserStreak) -> None:
        if streak.next_row is not None and streak.next_row.participant_id is not None:
            beaten_participant_ids.add(streak.next_row.participant_id)

    for (identity, level, _event_ids), progression in zip(
        course_specs, course_progressions, strict=True
    ):
        if level == GLOBAL_LEVEL:
            global_user_runs_by_identity[identity.identity_key] = {
                row.run_result_id
                for row in progression
                if row.participant_id in participant_ids
            }
        for streak in _user_streaks(progression, participant_ids):
            raw_course.append((identity, level, streak))
            collect(streak)

    for (identity, age_group, _event_ids), progression in zip(
        age_specs, age_progressions, strict=True
    ):
        for streak in _user_streaks(progression, participant_ids):
            raw_age.append((identity, age_group, streak))
            collect(streak)

    beaten_names = _participant_display_names(db, beaten_participant_ids)

    # Схлопывание «глобальный + своя система»: если серия системы совпадает с
    # глобальной по итоговому результату и судьбе (кем перебита), показываем
    # только глобальную запись.
    global_streak_keys = {
        _streak_key(identity, streak)
        for identity, level, streak in raw_course
        if level == GLOBAL_LEVEL
    }

    course_entries: list[dict[str, object]] = []
    milestones: list[dict[str, object]] = []
    for identity, level, streak in raw_course:
        if level not in (None, GLOBAL_LEVEL) and _streak_key(identity, streak) in global_streak_keys:
            continue  # системная серия «поглощена» глобальной записью — не дублируем
        course_entries.append(
            _entry(identity, streak, level=level, age_group=None, beaten_names=beaten_names)
        )

    # Вехи собираем по системным/моно прогрессиям (глобальная рекордная строка —
    # всегда и системная), охват помечаем по членству в глобальной прогрессии.
    for identity, level, streak in raw_course:
        if level == GLOBAL_LEVEL:
            continue
        identity_global_runs = global_user_runs_by_identity.get(identity.identity_key, set())
        for row in streak.rows:
            record_scope = None
            if identity.multi_system:
                record_scope = (
                    GLOBAL_LEVEL if row.run_result_id in identity_global_runs else row.platform_code
                )
            milestones.append(
                _milestone_row(
                    identity, row, kind="location_course_record", record_scope=record_scope, age_group=None
                )
            )

    age_entries: list[dict[str, object]] = []
    for identity, age_group, streak in raw_age:
        age_entries.append(
            _entry(identity, streak, level=None, age_group=age_group, beaten_names=beaten_names)
        )
        for row in streak.rows:
            milestones.append(
                _milestone_row(
                    identity, row, kind="location_age_group_record", record_scope=None, age_group=age_group
                )
            )

    _sort_entries(course_entries)
    _sort_entries(age_entries)

    payload["course"] = {
        "current_count": sum(1 for entry in course_entries if entry["is_current"]),
        "lost_count": sum(1 for entry in course_entries if not entry["is_current"]),
        "entries": course_entries,
    }
    payload["age_group"] = {
        "current_count": sum(1 for entry in age_entries if entry["is_current"]),
        "lost_count": sum(1 for entry in age_entries if not entry["is_current"]),
        "entries": age_entries,
    }
    payload["milestones"] = milestones
    return payload


def get_user_location_records(
    db: Session, user_id: UUID, *, use_cache: bool = True, force_refresh: bool = False
) -> dict[str, object]:
    key = user_location_records_cache_key(user_id)
    if use_cache and not force_refresh:
        try:
            raw = get_redis_client().get(key)
        except redis.RedisError:
            raw = None
        if isinstance(raw, str):
            try:
                return cast(dict[str, object], json.loads(raw))
            except (TypeError, ValueError):
                pass
    payload = compute_user_location_records(db, user_id)
    if use_cache:
        try:
            get_redis_client().setex(
                key, LOCATION_RECORDS_CACHE_TTL_SECONDS, json.dumps(payload, default=str)
            )
        except redis.RedisError:
            pass
    return payload
