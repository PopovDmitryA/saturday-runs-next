"""Рейтинг рекордов локаций: абсолютный зачёт и зачёт по возрастным группам.

Перенос Grafana-дашборда «Рекорды по возрастным группам в локациях»: там был
один селектор категории («М30-34») и таблица всех площадок с рекордом именно в
ней. На сайте рейтинг двухрежимный (решение Дмитрия 22.08.2026):

* «Абсолютный» — лучшее время площадки среди мужчин или женщин, сквозь все
  системы её истории (parkrun-эра + текущая система). Работает везде, включая
  S95 и RunPark, и потому режим по умолчанию.
* «Возрастной» — рекорд площадки в выбранной группе. Считается только по
  5 вёрст: у S95 категории нет вовсе, parkrun пишет в age_category age grade
  («54.38%»), а RunPark даёт parkrun-коды лишь у 41% строк — подмешивать его
  рекорд, посчитанный по неполному протоколу, мы не хотим (то же решение, что
  у возрастных рекордов на странице локации, см. FIVE_VERST_PLATFORM_CODE).

Строка рейтинга — каноническая идентичность каталога, а не отдельная локация
системы: «Кузьминки» одна строка, даже если площадка прожила parkrun и 5 вёрст.
Набор строк тот же, что в каталоге локаций (_collect_catalog_identities), —
зарубежный parkrun и псевдоплощадки туда не попадают.

Как считается. Оба разреза считаются одним снапшотом на всю страну и лежат в
Redis: срезы (пол, группа, система) — это фильтрация уже посчитанного, а не
новый проход по 2.2 млн результатов. Иначе каждый чих селектора стоил бы
полного скана run_results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, cast
from uuid import UUID

import redis
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import Event, Participant, Platform, PlatformLink, RunResult
from app.services.gender_position_service import gender_from_age_category
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_page_service import (
    FIVE_VERST_PLATFORM_CODE,
    _age_group_sort_key,
    _collect_catalog_identities,
    _first_by_platform_order,
    _gender_expression,
    _identity_display_name,
    _identity_status,
    _participant_display_names,
    _platform_link_join,
    _platform_order_index,
    _sort_identity_locations,
    age_group_key,
    normalize_age_group,
)
from app.time_format import format_finish_time_display

RatingScope = Literal["absolute", "age_group"]
RATING_SCOPES: tuple[RatingScope, ...] = ("absolute", "age_group")

RatingGender = Literal["male", "female"]
RATING_GENDERS: tuple[RatingGender, ...] = ("male", "female")

# Фильтр «смотреть по одной системе» — как в остальных рейтингах. В возрастном
# зачёте он неприменим (там и так одна система) и молча игнорируется.
PLATFORM_FILTER_VALUES: tuple[str, ...] = ("all", "five_verst", "s95", "runpark", "parkrun")

PLATFORM_LABELS: dict[str, str] = {
    "five_verst": "5 вёрст",
    "s95": "С95",
    "runpark": "RunPark",
    "parkrun": "parkrun",
}

# Группа попадает в селектор, только если рекорд в ней есть хотя бы на пяти
# площадках: иначе список забивают единичные строки вроде «Ж90–94» (один
# результат на всю страну), и выбрать в нём нужное невозможно.
MIN_GROUP_LOCATIONS = 5

# …и только если ступень правдоподобна. «М120», «Ж105–109» — это обрезки
# старого парсера (см. комментарий к _AGE_UNDER_RE в location_page_service), а
# не бегуны за сотню: по 1–10 результатов, но на двух десятках площадок, так
# что порогом выше они не отсеиваются.
MAX_PLAUSIBLE_AGE = 100

RATING_CACHE_KEY = "location-records-rating:v1"
RATING_CACHE_TTL_SECONDS = 6 * 60 * 60


@dataclass
class _RecordRow:
    """Один рекорд: время, кто и когда его поставил."""

    finish_time_sec: int
    participant_id: UUID | None
    event_date: date | None
    platform_code: str


@dataclass
class _IdentityRecords:
    slug: str
    name: str
    city: str | None
    region: str | None
    is_paused: bool
    is_cancelled: bool
    platform_codes: list[str]
    # (система, пол) → рекорд системы. Абсолют «по всем» — минимум по системам:
    # так один снапшот обслуживает и общий зачёт, и фильтр по системе.
    absolute: dict[tuple[str, str], _RecordRow] = field(default_factory=dict)
    # (пол, группа) → рекорд группы (только 5 вёрст).
    age_groups: dict[tuple[str, str], _RecordRow] = field(default_factory=dict)


def _protocol_gender(age_category: str) -> str | None:
    """Пол по категории протокола 5 вёрст: «М35-39» → male.

    Делегирует единственному источнику разбора категорий
    (gender_position_service): своя копия молча разъехалась бы при смене букв.
    """
    return gender_from_age_category("five_verst", age_category.strip())


def _age_group_is_plausible(age_group: str) -> bool:
    age, _ = _age_group_sort_key(age_group)
    return age <= MAX_PLAUSIBLE_AGE


def _better(current: _RecordRow | None, candidate: _RecordRow) -> _RecordRow:
    """Рекорд — минимальное время; при равенстве побеждает более ранняя дата."""
    if current is None:
        return candidate
    if candidate.finish_time_sec < current.finish_time_sec:
        return candidate
    if candidate.finish_time_sec > current.finish_time_sec:
        return current
    if current.event_date is None:
        return candidate
    if candidate.event_date is not None and candidate.event_date < current.event_date:
        return candidate
    return current


def _identity_meta(
    db: Session, catalog_index: LocationCatalogIndex
) -> tuple[dict[str, _IdentityRecords], dict[UUID, str]]:
    """Строки рейтинга (одна на идентичность каталога) + карта location_id → идентичность."""
    identity_locations, location_id_to_identity = _collect_catalog_identities(db, catalog_index)
    identities: dict[str, _IdentityRecords] = {}
    for identity_key, members in identity_locations.items():
        catalog = catalog_index.get_for_identity_key(identity_key)
        ordered = _sort_identity_locations(catalog, members)
        primary_location, _primary_code = ordered[0]
        is_paused, is_cancelled = _identity_status(catalog_index, ordered)
        identities[identity_key] = _IdentityRecords(
            slug=primary_location.external_key.strip().lower(),
            name=_identity_display_name(catalog, ordered, catalog_index),
            city=cast("str | None", _first_by_platform_order(ordered, lambda loc: loc.city)),
            region=cast("str | None", _first_by_platform_order(ordered, lambda loc: loc.region)),
            is_paused=is_paused,
            is_cancelled=is_cancelled,
            platform_codes=sorted({code for _loc, code in ordered}, key=_platform_order_index),
        )
    return identities, location_id_to_identity


def _fill_absolute_records(
    db: Session,
    identities: dict[str, _IdentityRecords],
    location_id_to_identity: dict[UUID, str],
) -> None:
    """Лучшее время каждой площадки по полу и системе.

    DISTINCT ON вместо MIN(): вместе со временем нужны автор и дата, а не только
    цифра. Кросслинки (один старт в протоколах двух систем) специально не
    дедуплицируются — у зеркала то же время, на минимум оно не влияет, а
    строку «рекорд системы» такая система и не получает: её протокол чужой.
    """
    location_ids = list(location_id_to_identity.keys())
    if not location_ids:
        return
    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    rows = (
        db.query(
            Event.location_id.label("location_id"),
            Platform.code.label("platform_code"),
            gender_expr.label("gender"),
            RunResult.finish_time_sec.label("finish_time_sec"),
            RunResult.participant_id.label("participant_id"),
            Event.event_date.label("event_date"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .filter(
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
        )
        .distinct(Event.location_id, Platform.code, gender_expr)
        .order_by(
            Event.location_id,
            Platform.code,
            gender_expr,
            RunResult.finish_time_sec,
            Event.event_date,
        )
        .all()
    )
    for row in rows:
        if row.gender not in ("male", "female"):
            continue
        identity = identities.get(location_id_to_identity[row.location_id])
        if identity is None:
            continue
        key = (row.platform_code, row.gender)
        identity.absolute[key] = _better(
            identity.absolute.get(key),
            _RecordRow(
                finish_time_sec=int(row.finish_time_sec),
                participant_id=row.participant_id,
                event_date=row.event_date,
                platform_code=row.platform_code,
            ),
        )


def _fill_age_group_records(
    db: Session,
    identities: dict[str, _IdentityRecords],
    location_id_to_identity: dict[UUID, str],
) -> None:
    """Рекорд каждой площадки в каждой возрастной группе — только по 5 вёрст.

    Из БД берём лучший результат на СЫРУЮ категорию протокола, а сливаем в
    нормализованную группу уже в Python (normalize_age_group): в «10–14»
    ложатся и «М10-14», и «М11-14» — обрезки старого парсера, и правила слияния
    незачем повторять второй раз на SQL.
    """
    location_ids = list(location_id_to_identity.keys())
    if not location_ids:
        return
    rows = (
        db.query(
            Event.location_id.label("location_id"),
            RunResult.age_category.label("age_category"),
            RunResult.finish_time_sec.label("finish_time_sec"),
            RunResult.participant_id.label("participant_id"),
            Event.event_date.label("event_date"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
            Platform.code == FIVE_VERST_PLATFORM_CODE,
            RunResult.age_category.isnot(None),
            RunResult.age_category != "",
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
        )
        .distinct(Event.location_id, RunResult.age_category)
        .order_by(
            Event.location_id,
            RunResult.age_category,
            RunResult.finish_time_sec,
            Event.event_date,
        )
        .all()
    )
    for row in rows:
        gender = _protocol_gender(row.age_category)
        age_group = normalize_age_group(row.age_category)
        if gender is None or age_group is None or not _age_group_is_plausible(age_group):
            continue
        identity = identities.get(location_id_to_identity[row.location_id])
        if identity is None:
            continue
        key = (gender, age_group)
        identity.age_groups[key] = _better(
            identity.age_groups.get(key),
            _RecordRow(
                finish_time_sec=int(row.finish_time_sec),
                participant_id=row.participant_id,
                event_date=row.event_date,
                platform_code=FIVE_VERST_PLATFORM_CODE,
            ),
        )


def _snapshot_payload(db: Session) -> dict[str, object]:
    """Полный расчёт: все площадки, оба зачёта, все группы — одним куском."""
    catalog_index = LocationCatalogIndex(db)
    identities, location_id_to_identity = _identity_meta(db, catalog_index)
    _fill_absolute_records(db, identities, location_id_to_identity)
    _fill_age_group_records(db, identities, location_id_to_identity)

    participant_ids = {
        record.participant_id
        for identity in identities.values()
        for record in (*identity.absolute.values(), *identity.age_groups.values())
        if record.participant_id is not None
    }
    names = _participant_display_names(db, participant_ids)

    def _entry(record: _RecordRow, *first: str) -> list[object]:
        name, handle = names.get(record.participant_id, (None, None)) if record.participant_id else (None, None)
        return [
            *first,
            record.finish_time_sec,
            record.event_date.isoformat() if record.event_date else None,
            name,
            handle,
            record.platform_code,
        ]

    locations: list[dict[str, object]] = []
    for identity in identities.values():
        locations.append(
            {
                "slug": identity.slug,
                "name": identity.name,
                "city": identity.city,
                "region": identity.region,
                "is_paused": identity.is_paused,
                "is_cancelled": identity.is_cancelled,
                "platform_codes": identity.platform_codes,
                "absolute": [
                    _entry(record, platform_code, gender)
                    for (platform_code, gender), record in identity.absolute.items()
                ],
                "age_groups": [
                    _entry(record, gender, age_group)
                    for (gender, age_group), record in identity.age_groups.items()
                ],
            }
        )
    locations.sort(key=lambda item: str(item["name"]).lower())
    return {"locations": locations}


def _read_cache() -> dict[str, object] | None:
    try:
        raw = get_redis_client().get(RATING_CACHE_KEY)
    except redis.RedisError:
        return None
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return cast("dict[str, object]", payload) if isinstance(payload, dict) else None


def _write_cache(payload: dict[str, object]) -> None:
    try:
        get_redis_client().setex(RATING_CACHE_KEY, RATING_CACHE_TTL_SECONDS, json.dumps(payload, default=str))
    except redis.RedisError:
        return


def invalidate_location_records_rating_cache() -> None:
    """Ручной сброс снапшота. Штатно рейтинг живёт до истечения TTL — как
    каталог локаций: протоколы приезжают раз в неделю, и вешать инвалидацию на
    каждую batch-джобу синка дороже, чем стоит эта свежесть."""
    try:
        get_redis_client().delete(RATING_CACHE_KEY)
    except redis.RedisError:
        return


def _snapshot(db: Session, *, use_cache: bool = True, refresh: bool = False) -> dict[str, object]:
    if use_cache and not refresh:
        cached = _read_cache()
        if cached is not None:
            return cached
    payload = _snapshot_payload(db)
    if use_cache:
        _write_cache(payload)
    return payload


def refresh_location_records_rating_cache(db: Session) -> int:
    """Пересчитать снапшот, не дожидаясь TTL. Зовётся прогревом рейтингов.

    Один снапшот обслуживает всю сетку кнопок (зачёт × пол × группа × система),
    поэтому прогрев здесь — ровно один расчёт, а не десятки.
    """
    payload = _snapshot(db, use_cache=True, refresh=True)
    locations = cast("list[dict[str, Any]]", payload.get("locations", []))
    return len(locations)


def _absolute_record(entries: list[list[Any]], gender: str, platform: str) -> list[Any] | None:
    """Рекорд площадки в абсолюте: по выбранной системе или лучший среди всех."""
    best: list[Any] | None = None
    for entry in entries:
        entry_platform, entry_gender = entry[0], entry[1]
        if entry_gender != gender:
            continue
        if platform != "all" and entry_platform != platform:
            continue
        if best is None or entry[2] < best[2]:
            best = entry
    return best


def _age_group_record(entries: list[list[Any]], gender: str, age_group: str) -> list[Any] | None:
    for entry in entries:
        if entry[0] == gender and entry[1] == age_group:
            return entry
    return None


def available_age_groups(snapshot: dict[str, object], gender: str) -> list[dict[str, object]]:
    """Группы для селектора: только те, где рекорд есть хотя бы на MIN_GROUP_LOCATIONS площадках."""
    counts: dict[str, int] = {}
    for location in cast("list[dict[str, Any]]", snapshot.get("locations", [])):
        for entry in location.get("age_groups", []):
            if entry[0] != gender:
                continue
            counts[entry[1]] = counts.get(entry[1], 0) + 1
    return [
        {"age_group": age_group, "locations_count": count, "key": age_group_key(gender, age_group)}
        for age_group, count in sorted(counts.items(), key=lambda item: _age_group_sort_key(item[0]))
        if count >= MIN_GROUP_LOCATIONS
    ]


def _row_payload(location: dict[str, Any], entry: list[Any]) -> dict[str, object]:
    finish_time_sec, event_date, runner_name, runner_handle, platform_code = entry[2:7]
    return {
        "slug": location["slug"],
        "name": location["name"],
        "city": location.get("city"),
        "region": location.get("region"),
        "is_paused": bool(location.get("is_paused")),
        "is_cancelled": bool(location.get("is_cancelled")),
        "finish_time_sec": int(finish_time_sec),
        "finish_time_display": format_finish_time_display(int(finish_time_sec)),
        "runner_name": runner_name,
        "runner_handle": runner_handle,
        "event_date": event_date,
        "platform_code": platform_code,
        "platform_label": PLATFORM_LABELS.get(platform_code, platform_code),
        # Дата ведёт в наш протокол этого старта, а не на сайт системы.
        "protocol_url": (f"/locations/{location['slug']}/protocol/{platform_code}/{event_date}" if event_date else None),
    }


def normalize_scope(scope: str) -> RatingScope:
    return cast("RatingScope", scope) if scope in RATING_SCOPES else "absolute"


def normalize_gender(gender: str) -> RatingGender:
    return cast("RatingGender", gender) if gender in RATING_GENDERS else "male"


def normalize_platform(platform: str, scope: RatingScope) -> str:
    # В возрастном зачёте системы всего одна — фильтр там не имеет смысла и
    # схлопывается в «все», иначе ссылка вида ?platform=s95 отдавала бы пустую
    # таблицу вместо рейтинга.
    if scope == "age_group":
        return "all"
    return platform if platform in PLATFORM_FILTER_VALUES else "all"


def build_location_records_rating(
    db: Session,
    *,
    scope: str = "absolute",
    gender: str | None = None,
    age_group: str | None = None,
    platform: str = "all",
    viewer_group: dict[str, str] | None = None,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, object]:
    """Таблица рейтинга: одна строка на площадку, места по времени рекорда.

    Ничего не выбравший зритель попадает в свой зачёт: пол и ступень берутся из
    его последней пробежки на 5 вёрст (viewer_group). Явный выбор в интерфейсе
    всегда сильнее — он приходит параметрами и перекрывает подстановку.
    """
    resolved_scope = normalize_scope(scope)
    resolved_gender = normalize_gender(gender if gender is not None else (viewer_group or {}).get("gender", ""))
    resolved_platform = normalize_platform(platform, resolved_scope)
    snapshot = _snapshot(db, use_cache=use_cache, refresh=refresh)
    locations = cast("list[dict[str, Any]]", snapshot.get("locations", []))
    groups = available_age_groups(snapshot, resolved_gender)

    resolved_age_group: str | None = None
    if resolved_scope == "age_group":
        # Группу сверяем со снапшотом, а не со списком селектора: порог
        # MIN_GROUP_LOCATIONS прячет редкие ступени из выпадашки, но прямая
        # ссылка на «Ж90–94» должна открывать её таблицу, а не молча
        # подменять группу на самую массовую.
        known = {
            str(entry[1])
            for location in locations
            for entry in location.get("age_groups", [])
            if entry[0] == resolved_gender
        }
        viewer_age = (viewer_group or {}).get("age_group")
        if age_group in known:
            resolved_age_group = age_group
        elif viewer_group is not None and viewer_group.get("gender") == resolved_gender and viewer_age in known:
            resolved_age_group = viewer_age
        else:
            # Незнакомая (или не переданная) группа — не 404: открываем самую
            # массовую ступень, чтобы страница по «голой» ссылке была осмысленной.
            resolved_age_group = _default_age_group(groups)

    rows: list[dict[str, object]] = []
    for location in locations:
        if resolved_scope == "absolute":
            entry = _absolute_record(location.get("absolute", []), resolved_gender, resolved_platform)
        elif resolved_age_group is None:
            entry = None
        else:
            entry = _age_group_record(location.get("age_groups", []), resolved_gender, resolved_age_group)
        if entry is None:
            continue
        rows.append(_row_payload(location, entry))

    rows.sort(key=lambda row: (cast("int", row["finish_time_sec"]), str(row["name"]).lower()))
    # Место = RANK: одинаковое время делит место, следующее за ничьёй
    # пропускается (как в остальных рейтингах сайта).
    previous_time: int | None = None
    previous_place = 0
    for index, row in enumerate(rows, start=1):
        current_time = cast("int", row["finish_time_sec"])
        if previous_time is not None and current_time == previous_time:
            row["place"] = previous_place
            continue
        row["place"] = index
        previous_place = index
        previous_time = current_time

    return {
        "scope": resolved_scope,
        "gender": resolved_gender,
        "age_group": resolved_age_group,
        "platform": resolved_platform,
        "rows": rows,
        "age_groups": groups,
        "platforms": list(PLATFORM_FILTER_VALUES),
        # Своя ступень зрителя — витрина подписывает ею пункт селектора («ваша»).
        "viewer_age_group": (viewer_group or {}).get("age_group"),
        "viewer_gender": (viewer_group or {}).get("gender"),
    }


def _default_age_group(groups: list[dict[str, object]]) -> str | None:
    """Группа по умолчанию — самая массовая: у неё рекорд есть почти везде."""
    if not groups:
        return None
    return str(max(groups, key=lambda item: cast("int", item["locations_count"]))["age_group"])


def viewer_age_group(db: Session, user_id: UUID | None) -> dict[str, str] | None:
    """Возрастная группа зрителя по его последней пробежке на 5 вёрст.

    Нужна, чтобы «Возрастной» зачёт открывался сразу на своей ступени: человек
    приходит в этот рейтинг посмотреть, как выглядят рекорды именно его
    категории. Берём последнюю по дате, а не первую: категория меняется с
    возрастом, и актуальная — та, в которой он бежит сейчас.
    """
    if user_id is None:
        return None
    participant_ids = (
        db.query(Participant.id)
        .join(PlatformLink, _platform_link_join())
        .join(Platform, Participant.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id, Platform.code == FIVE_VERST_PLATFORM_CODE)
        .all()
    )
    ids = [row[0] for row in participant_ids]
    if not ids:
        return None
    row = (
        db.query(RunResult.age_category)
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id.in_(ids),
            RunResult.age_category.isnot(None),
            RunResult.age_category != "",
        )
        .order_by(Event.event_date.desc())
        .first()
    )
    if row is None:
        return None
    gender = _protocol_gender(row[0])
    age_group = normalize_age_group(row[0])
    if gender is None or age_group is None or not _age_group_is_plausible(age_group):
        return None
    return {"gender": gender, "age_group": age_group}
