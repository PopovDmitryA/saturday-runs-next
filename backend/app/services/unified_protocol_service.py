"""Единый протокол недели — все площадки всех систем в одном забеге.

Перенос дашборда Grafana «Единый протокол»: сквозной протокол по всем
локациям за дату, где все финишёры выстроены по времени. Отличия от легаси:

- **неделя, а не дата.** RunPark частью площадок бегает в воскресенье, 5 вёрст
  изредка в пятницу, у parkrun бывают четверговые праздничные старты. Ровняем
  по календарной неделе (пн–вс) и подписываем её субботой — так «суббота»
  остаётся целой, а воскресный RunPark не выпадает в отдельный протокол;
- **все системы сразу**, с фильтром «Система»: выбрал 5 вёрст — единый
  протокол пересобрался внутри одной системы, места пересчитались;
- **три зачёта**: абсолютный, среди своего пола и внутри своей возрастной
  группы. Возрастная группа берётся из категории самого протокола и сводится к
  общему виду («М35-39», «VM35-39» → «35–39»), поэтому 5 вёрст и RunPark
  считаются в одних ступенях. У S95 категорий нет вовсе, у parkrun в протоколе
  лежит age grade — их строки в групповом зачёте не участвуют (категория
  профиля parkrun — ТЕКУЩАЯ, на дату старта она врала бы).

Кэшируется сырой список строк недели (без мест): места зависят от выбранной
системы и считаются на запросе — это сортировка 16 тыс. строк, доли
миллисекунды. Список сжимается: неделя пикового декабря — это ~16 тыс. строк,
без сжатия в Redis уехало бы несколько мегабайт.
"""

from __future__ import annotations

import base64
import json
import zlib
from bisect import bisect_left
from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import redis
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.location_page_url import PLATFORM_ORDER
from app.models import (
    Event,
    EventCrosslink,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services.gender_position_service import GENDER_FEMALE, GENDER_MALE
from app.services.location_catalog_service import (
    PARKRUN_PLATFORM_CODE,
    LocationCatalogIndex,
    russian_parkrun_location_ids,
)
from app.services.location_page_service import (
    _age_group_sort_key,
    _dedupe_crosslinked_events,
    _platform_link_join,
    _sort_identity_locations,
    normalize_age_group,
)
from app.services.location_protocol_service import _age_grade, _row_gender
from app.services.platform_titles import PLATFORM_TITLES
from app.time_format import format_finish_time_display, normalize_finish_time_display

# Тот же TTL, что у протокола локации: субботним вечером протоколы приезжают
# частями, и залипший на полдня кэш показывал бы половину страны.
UNIFIED_PROTOCOL_CACHE_TTL_SECONDS = 3 * 60 * 60
# Список недель — тот же TTL, что у самой недели: субботним вечером в нём
# появляется новая строка, и залипший на сутки список оставил бы стрелку
# «следующая неделя» пустой ровно в тот день, когда её и жмут.
WEEKS_CACHE_TTL_SECONDS = UNIFIED_PROTOCOL_CACHE_TTL_SECONDS

UNIFIED_PROTOCOL_CACHE_VERSION = "v1"

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

GENDER_FILTERS = ("male", "female")


def week_start_of(anchor: date) -> date:
    """Понедельник недели, которой принадлежит дата."""
    return anchor - timedelta(days=anchor.weekday())


def saturday_of(anchor: date) -> date:
    """Суббота недели — подпись недели во всех адресах и заголовках."""
    return week_start_of(anchor) + timedelta(days=5)


def unified_protocol_cache_key(saturday: date) -> str:
    return f"protocol:week:{UNIFIED_PROTOCOL_CACHE_VERSION}:{saturday.isoformat()}"


def unified_protocol_weeks_cache_key() -> str:
    return f"protocol:weeks:{UNIFIED_PROTOCOL_CACHE_VERSION}"


def invalidate_unified_protocol_cache(saturday: date) -> None:
    try:
        client = get_redis_client()
        client.delete(unified_protocol_cache_key(saturday))
        client.delete(unified_protocol_weeks_cache_key())
    except redis.RedisError:
        pass


def _read_packed_cache(key: str) -> dict[str, Any] | None:
    try:
        raw = get_redis_client().get(key)
    except redis.RedisError:
        return None
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(zlib.decompress(base64.b64decode(raw)).decode("utf-8"))
    except (TypeError, ValueError, zlib.error):
        return None
    return payload if isinstance(payload, dict) else None


def _write_packed_cache(key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
    try:
        packed = base64.b64encode(
            zlib.compress(json.dumps(payload, default=str).encode("utf-8"), 6)
        ).decode("ascii")
        get_redis_client().setex(key, ttl_seconds, packed)
    except redis.RedisError:
        pass


def list_protocol_weeks(db: Session, *, use_cache: bool = True, refresh: bool = False) -> list[dict[str, Any]]:
    """Недели, в которых есть хоть один протокол: подпись-суббота и цифры.

    Нужны навигации: стрелки «предыдущая/следующая» и выпадающий список. Скан
    всех результатов стоит пару секунд, поэтому список живёт в кэше сутки —
    новая неделя появляется раз в неделю.
    """
    key = unified_protocol_weeks_cache_key()
    if use_cache and not refresh:
        cached = _read_packed_cache(key)
        if cached is not None:
            return list(cached.get("weeks") or [])

    # Зарубежный parkrun выкидываем и здесь: иначе в списке недель у старой
    # недели стояло бы «3 761», а на самой странице — «3 373 финишёра».
    # Считаем «оставить» через белый список русских parkrun-локаций (их около
    # сотни), а не через чёрный список зарубежных (их 2,5 тысячи).
    russian_parkrun = russian_parkrun_location_ids(db)
    in_scope = (Platform.code != PARKRUN_PLATFORM_CODE) | Event.location_id.in_(russian_parkrun)
    # …и вторичные события кросслинков, как в самом протоколе. По всей базе
    # первичное всегда на месте, так что достаточно выкинуть вторичные.
    secondary_events = db.query(EventCrosslink.secondary_event_id).scalar_subquery()
    rows = (
        db.query(
            func.date_trunc("week", Event.event_date).label("week_start"),
            func.count(RunResult.id).label("finishers"),
            func.count(func.distinct(Event.id)).label("events"),
        )
        .join(RunResult, RunResult.event_id == Event.id)
        .join(Platform, Platform.id == Event.platform_id)
        .filter(Event.is_test_event.is_(False), in_scope, Event.id.notin_(secondary_events))
        .group_by("week_start")
        .all()
    )
    weeks = [
        {
            "saturday": (row.week_start.date() + timedelta(days=5)).isoformat(),
            "finishers": int(row.finishers or 0),
            "events": int(row.events or 0),
        }
        for row in rows
    ]
    weeks.sort(key=lambda item: item["saturday"])

    if use_cache:
        _write_packed_cache(key, {"weeks": weeks}, WEEKS_CACHE_TTL_SECONDS)
    return weeks


def latest_protocol_saturday(db: Session) -> date | None:
    """Неделя, которую открывает адрес без даты.

    Считается отдельным дешёвым запросом, а не по кэшу списка недель: в
    субботу утром протоколы только начинают приезжать, и «последняя неделя»
    обязана переключиться сразу, а не когда протухнет список.

    Фильтр по зарубежному parkrun — тот же, что в списке недель и в самом
    протоколе: иначе свежесинканный забег туриста за границей (спецстарт
    четверга, ранняя суббота на востоке) открывал бы по умолчанию неделю, в
    которой на странице ноль строк и которой нет в списке недель.
    """
    russian_parkrun = russian_parkrun_location_ids(db)
    in_scope = (Platform.code != PARKRUN_PLATFORM_CODE) | Event.location_id.in_(russian_parkrun)
    last_event_date = (
        db.query(func.max(Event.event_date))
        .select_from(Event)
        .join(Platform, Platform.id == Event.platform_id)
        .filter(Event.is_test_event.is_(False), in_scope)
        .scalar()
    )
    return saturday_of(last_event_date) if last_event_date is not None else None


def _location_directory(db: Session) -> tuple[dict[UUID, dict[str, Any]], set[UUID]]:
    """location_id → как эту площадку показать, плюс площадки вне зачёта.

    Слаг берём тот же, что у страницы локации: это external_key основной
    локации идентичности (см. resolve_location_identity), иначе ссылка из
    единого протокола вела бы на 404 у площадок, сменивших систему.

    Вне зачёта — ЗАРУБЕЖНЫЙ parkrun. От такой площадки в БД лежат не протокол,
    а строки наших туристов, вытянутые из их профилей; вдобавок среди них
    попадаются junior parkrun на 2 км. В едином протоколе 2019 года такая
    строка вылезала на первое место с «09:02» — быстрее любой пятёрки. Русский
    parkrun 2014–2022 собран протоколами целиком и в зачёте остаётся.
    """
    catalog_index = LocationCatalogIndex(db)
    rows = db.query(Location, Platform.code).join(Platform, Location.platform_id == Platform.id).all()
    russian_parkrun = russian_parkrun_location_ids(db, catalog_index)

    identity_locations: dict[str, list[tuple[Location, str]]] = defaultdict(list)
    for location, platform_code in rows:
        identity_locations[catalog_index.canonical_identity_key(location, platform_code)].append(
            (location, platform_code)
        )

    directory: dict[UUID, dict[str, Any]] = {}
    excluded: set[UUID] = set()
    for identity_key, members in identity_locations.items():
        ordered = _sort_identity_locations(catalog_index.get_for_identity_key(identity_key), members)
        slug = ordered[0][0].external_key.strip().lower()
        for location, platform_code in members:
            if platform_code == PARKRUN_PLATFORM_CODE and location.id not in russian_parkrun:
                excluded.add(location.id)
                continue
            directory[location.id] = {
                "slug": slug,
                # Имя — актуальное название площадки во всех системах сразу
                # (parkrun-эпоха показывается по-русски, как в журнале).
                "name": catalog_index.display_name(location, platform_code),
                "city": location.city,
                "country": location.country,
            }
    return directory, excluded


def _compute_week_rows(db: Session, saturday: date) -> dict[str, Any]:
    """Сырые строки недели — без мест: места зависят от выбранной системы."""
    week_start = week_start_of(saturday)
    week_end = week_start + timedelta(days=6)

    event_rows = (
        db.query(Event.id, Event.location_id, Event.event_date, Event.event_number, Platform.code)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            Event.event_date >= week_start,
            Event.event_date <= week_end,
            Event.is_test_event.is_(False),
        )
        .all()
    )
    # Один физический старт, попавший в протоколы двух систем, иначе удвоил бы
    # своих финишёров в общем зачёте.
    kept_ids = _dedupe_crosslinked_events(db, [row.id for row in event_rows])
    events = {row.id: row for row in event_rows if row.id in kept_ids}

    payload: dict[str, Any] = {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "saturday": saturday.isoformat(),
        "rows": [],
        "events_count": 0,
        "locations_count": 0,
        "skipped_foreign_parkrun": 0,
    }
    if not events:
        return payload

    directory, excluded_locations = _location_directory(db)

    result_rows = (
        db.query(
            RunResult.id,
            RunResult.event_id,
            RunResult.position,
            RunResult.finish_time_sec,
            RunResult.finish_time_display,
            RunResult.pace_display,
            RunResult.age_category,
            RunResult.club_name,
            RunResult.status,
            RunResult.participant_id,
            RunResult.is_pr,
            RunResult.is_first_run,
            Participant.display_name,
            Participant.external_user_id,
            Participant.gender,
            Participant.age_category.label("participant_age_category"),
            Participant.club_name.label("participant_club"),
            User.serial_id,
            User.profile_private,
        )
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .outerjoin(User, PlatformLink.user_id == User.id)
        .filter(RunResult.event_id.in_(list(events.keys())))
        .all()
    )

    rows: list[dict[str, Any]] = []
    seen: set[UUID] = set()
    used_events: set[UUID] = set()
    used_locations: set[str] = set()
    skipped_foreign = 0

    for row in result_rows:
        # Привязка одного участника к нескольким аккаунтам сайта размножает
        # строку протокола — как в location_protocol_service.
        if row.id in seen:
            continue
        seen.add(row.id)

        event = events[row.event_id]
        if event.location_id in excluded_locations:
            skipped_foreign += 1
            continue
        platform_code = event.code
        place_info = directory.get(event.location_id) or {
            "slug": None,
            "name": "",
            "city": None,
            "country": None,
        }

        raw_category = (row.age_category or "").strip() or None
        age_group = normalize_age_group(raw_category)
        gender = _row_gender(platform_code, raw_category, row.gender, row.participant_age_category)
        display_name = (row.display_name or "").strip()
        is_unknown = (
            row.participant_id is None
            or (row.status or "").strip().lower() in ("unknown", "unknown_runner")
            or display_name.lower() in ("неизвестный", "unknown")
        )
        finish_time_sec = row.finish_time_sec if row.finish_time_sec else None

        used_events.add(row.event_id)
        if place_info["slug"]:
            used_locations.add(str(place_info["slug"]))

        rows.append(
            {
                "name": row.display_name,
                "external_user_id": row.external_user_id,
                "serial_id": row.serial_id if row.profile_private is False else None,
                "is_unknown": is_unknown,
                "gender": gender,
                "age_category": raw_category if age_group else None,
                "age_group": age_group,
                "age_grade": _age_grade(raw_category),
                "finish_time_sec": finish_time_sec,
                "finish_time_display": normalize_finish_time_display(
                    finish_time_sec, row.finish_time_display
                ),
                "pace_display": row.pace_display,
                "club_name": (row.club_name or row.participant_club or "").strip() or None,
                "platform_code": platform_code,
                "location_slug": place_info["slug"],
                "location_name": place_info["name"],
                "city": place_info["city"],
                "country": place_info["country"],
                "event_date": event.event_date.isoformat(),
                "event_number": event.event_number,
                "location_position": row.position,
                "is_pr": bool(row.is_pr),
                "is_first_run": bool(row.is_first_run),
            }
        )

    # Сортировка одна на все зачёты: по времени, дальше по месту на своей
    # площадке — так строки без времени (DNF, дисквалификация) уходят в хвост
    # и не мешают местам.
    rows.sort(
        key=lambda item: (
            item["finish_time_sec"] is None,
            item["finish_time_sec"] or 0,
            item["location_position"] or 0,
            (item["name"] or ""),
        )
    )

    payload["volunteers"] = _volunteer_counts(db, events)
    payload["rows"] = rows
    payload["events_count"] = len(used_events)
    payload["locations_count"] = len(used_locations)
    payload["skipped_foreign_parkrun"] = skipped_foreign
    return payload


def _week_rows(db: Session, saturday: date, *, use_cache: bool, refresh: bool) -> dict[str, Any]:
    cache_key = unified_protocol_cache_key(saturday)
    payload: dict[str, Any] | None = None
    if use_cache and not refresh:
        payload = _read_packed_cache(cache_key)
    if payload is None:
        payload = _compute_week_rows(db, saturday)
        if use_cache:
            _write_packed_cache(cache_key, payload, UNIFIED_PROTOCOL_CACHE_TTL_SECONDS)
    return payload


def _assign_places(rows: list[dict[str, Any]]) -> None:
    """Три зачёта разом: абсолютный, по полу и внутри пола по возрастной группе.

    rows уже отсортированы по времени — местам достаточно одного прохода.
    Строки без времени мест не получают вовсе: в протоколе платформы у них
    стоит статус, а не финиш.
    """
    gender_seen: dict[str, int] = defaultdict(int)
    group_seen: dict[tuple[str, str], int] = defaultdict(int)
    gender_total: dict[str, int] = defaultdict(int)
    group_total: dict[tuple[str, str], int] = defaultdict(int)

    for row in rows:
        if row["finish_time_sec"] is None:
            continue
        if row["gender"]:
            gender_total[row["gender"]] += 1
            if row["age_group"]:
                group_total[(row["gender"], row["age_group"])] += 1

    place = 0
    for row in rows:
        if row["finish_time_sec"] is None:
            row["place"] = None
            row["gender_place"] = None
            row["gender_total"] = None
            row["age_group_place"] = None
            row["age_group_total"] = None
            continue
        place += 1
        row["place"] = place
        gender = row["gender"]
        if gender:
            gender_seen[gender] += 1
            row["gender_place"] = gender_seen[gender]
            row["gender_total"] = gender_total[gender]
        else:
            row["gender_place"] = None
            row["gender_total"] = None
        group_key = (gender, row["age_group"]) if gender and row["age_group"] else None
        if group_key is not None:
            group_seen[group_key] += 1
            row["age_group_place"] = group_seen[group_key]
            row["age_group_total"] = group_total[group_key]
        else:
            row["age_group_place"] = None
            row["age_group_total"] = None


def _assign_scope_places(rows: list[dict[str, Any]]) -> None:
    """Пересчёт колонки «№» под выбранный срез (пол и/или возрастная группа).

    Места по полу и по группе НЕ трогаем: они меряют результат по всей неделе
    своей системы, и от того, что человек открыл «только женщины», женщина не
    становится первой в протоколе — она первая среди женщин, и это уже стоит
    в своей колонке.
    """
    place = 0
    for row in rows:
        if row["finish_time_sec"] is None:
            row["place"] = None
            continue
        place += 1
        row["place"] = place


def _volunteer_counts(db: Session, events: dict[UUID, Any]) -> dict[str, dict[str, int]]:
    """Волонтёры недели по системам: записей и людей.

    Две цифры, а не одна: у 5 вёрст один человек за старт часто берёт несколько
    ролей, и «волонтёрств» всегда больше, чем волонтёров. Людей считаем по
    participant_id, то есть по аккаунту в системе — один и тот же человек,
    отволонтёривший и в 5 вёрстах, и в RunPark, посчитается дважды (связать их
    можно только через привязку аккаунтов, а она есть далеко не у всех).
    """
    counts: dict[str, dict[str, int]] = {}
    if not events:
        return counts
    rows = (
        db.query(VolunteerResult.event_id, VolunteerResult.participant_id)
        .filter(VolunteerResult.event_id.in_(list(events.keys())))
        .all()
    )
    people: dict[str, set[UUID]] = defaultdict(set)
    for event_id, participant_id in rows:
        platform_code = events[event_id].code
        bucket = counts.setdefault(platform_code, {"entries": 0, "people": 0})
        bucket["entries"] += 1
        if participant_id is not None:
            people[platform_code].add(participant_id)
    for platform_code, bucket in counts.items():
        bucket["people"] = len(people[platform_code])
    return counts


def _facet_rows(
    rows: list[dict[str, Any]],
    *,
    platform: str | None = None,
    gender: str | None = None,
    age_group: str | None = None,
) -> list[dict[str, Any]]:
    """Строки под перечисленные фильтры — для цифр в скобках у ФАСЕТА.

    Свой фильтр вызывающий просто не передаёт: иначе у него остался бы ровно
    один вариант с ненулевым числом и переключиться было бы некуда.
    """
    return [
        row
        for row in rows
        if (platform is None or row["platform_code"] == platform)
        and (gender is None or row["gender"] == gender)
        and (age_group is None or row["age_group"] == age_group)
    ]


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    times = sorted(row["finish_time_sec"] for row in rows if row["finish_time_sec"])
    genders: dict[str | None, int] = defaultdict(int)
    for row in rows:
        genders[row["gender"]] += 1

    def _best(gender: str) -> dict[str, Any] | None:
        candidates = [
            row for row in rows if row["gender"] == gender and row["finish_time_sec"]
        ]
        if not candidates:
            return None
        best = min(candidates, key=lambda row: row["finish_time_sec"])
        return {
            "name": best["name"],
            "time_display": best["finish_time_display"],
            "time_sec": best["finish_time_sec"],
            "location_name": best["location_name"],
            "location_slug": best["location_slug"],
            "platform_code": best["platform_code"],
        }

    median = None
    if times:
        middle = len(times) // 2
        median = times[middle] if len(times) % 2 else round((times[middle - 1] + times[middle]) / 2)

    return {
        "finishers": len(rows),
        "male": genders.get(GENDER_MALE, 0),
        "female": genders.get(GENDER_FEMALE, 0),
        "unknown_gender": genders.get(None, 0),
        # Площадки считаем по самим строкам: так цифра честна и для полного
        # зачёта, и для среза «женщины 40–44» — там площадок меньше.
        "locations": len({row["location_slug"] for row in rows if row["location_slug"]}),
        "avg_time_sec": round(sum(times) / len(times)) if times else None,
        "avg_time_display": format_finish_time_display(round(sum(times) / len(times))) if times else None,
        "median_time_sec": median,
        "median_time_display": format_finish_time_display(median) if median else None,
        "best_male": _best(GENDER_MALE),
        "best_female": _best(GENDER_FEMALE),
        "debutants": sum(1 for row in rows if row["is_first_run"]),
        "prs": sum(1 for row in rows if row["is_pr"]),
        "clubs_count": len({row["club_name"] for row in rows if row["club_name"]}),
    }


def _age_group_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = {}
    for row in rows:
        group = row["age_group"]
        if not group:
            continue
        bucket = buckets.setdefault(group, {"male": 0, "female": 0, "unknown": 0})
        if row["gender"] == GENDER_MALE:
            bucket["male"] += 1
        elif row["gender"] == GENDER_FEMALE:
            bucket["female"] += 1
        else:
            bucket["unknown"] += 1
    return [
        {
            "age_group": group,
            "male": counts["male"],
            "female": counts["female"],
            "unknown": counts["unknown"],
            "total": counts["male"] + counts["female"] + counts["unknown"],
        }
        for group, counts in sorted(buckets.items(), key=lambda pair: _age_group_sort_key(pair[0]))
    ]


def _platform_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finishers: dict[str, int] = defaultdict(int)
    locations: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        finishers[row["platform_code"]] += 1
        if row["location_slug"]:
            locations[row["platform_code"]].add(str(row["location_slug"]))
    order = {code: index for index, code in enumerate(PLATFORM_ORDER)}
    return [
        {
            "platform_code": code,
            "title": PLATFORM_TITLES.get(code, code),
            "finishers": count,
            "locations": len(locations[code]),
        }
        for code, count in sorted(
            finishers.items(), key=lambda pair: (order.get(pair[0], len(order)), pair[0])
        )
    ]


def _viewer_keys(db: Session, viewer: User | None) -> set[tuple[str, str]]:
    """(система, внешний id) всех аккаунтов участника — по ним ищется своя строка."""
    if viewer is None:
        return set()
    return {
        (code, external_user_id)
        for code, external_user_id in db.query(Platform.code, PlatformLink.external_user_id)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == viewer.id)
        .all()
        if external_user_id
    }


def _matches_query(row: dict[str, Any], needle: str) -> bool:
    haystack = " ".join(
        str(value)
        for value in (row["name"], row["location_name"], row["city"], row["club_name"])
        if value
    ).lower()
    return needle in haystack


def build_unified_protocol(
    db: Session,
    saturday: date,
    *,
    platform: str | None = None,
    gender: str | None = None,
    age_group: str | None = None,
    query: str | None = None,
    page: int = 1,
    per_page: int = DEFAULT_PAGE_SIZE,
    viewer: User | None = None,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    """Единый протокол недели в выбранном зачёте.

    Система, пол и возрастная группа — все три ЗАЧЁТЫ, а не просто фильтры
    показа: выбрал «5 вёрст» и «М40-44» — и колонка «№» пересчиталась 1, 2,
    3… внутри выбранного (правка Дмитрия 22.08.2026: «при выборе возрастной
    группы рейтинг тоже должен пересчитываться»). Колонки «М/Ж» и
    «Возр. группа» при этом продолжают показывать место в зачёте своей
    СИСТЕМЫ — по ним видно, где результат стоит в масштабе всей недели, а не
    только выбранного среза.

    Поиск по имени зачёт не трогает: он ищет строку, а не сужает соревнование.
    """
    week = _week_rows(db, saturday, use_cache=use_cache, refresh=refresh)
    all_rows: list[dict[str, Any]] = list(week.get("rows") or [])

    known_platforms = {row["platform_code"] for row in all_rows}
    scope_platform = platform if platform in known_platforms else None

    # Строки кэша общие на всех зрителей — копируем перед проставлением мест
    # и «моей» отметки.
    scope_rows = [
        dict(row)
        for row in all_rows
        if scope_platform is None or row["platform_code"] == scope_platform
    ]
    _assign_places(scope_rows)

    viewer_keys = _viewer_keys(db, viewer)
    my_rows: list[dict[str, Any]] = []
    for row in scope_rows:
        is_me = bool(viewer_keys) and (row["platform_code"], row["external_user_id"]) in viewer_keys
        row["is_me"] = is_me
        if is_me:
            my_rows.append(row)

    # Свои строки снимаем ДО пересчёта под срез: «Ваш результат» — про
    # человека, а не про текущий фильтр, и должен показывать место в зачёте
    # системы, даже когда в таблице открыт чужой пол или чужая группа.
    my_results = [dict(row) for row in my_rows]

    scope_gender = gender if gender in GENDER_FILTERS else None
    scope_age_group = age_group or None
    in_scope = [
        row
        for row in scope_rows
        if (scope_gender is None or row["gender"] == scope_gender)
        and (scope_age_group is None or row["age_group"] == scope_age_group)
    ]
    if scope_gender is not None or scope_age_group is not None:
        _assign_scope_places(in_scope)

    # Цифры в скобках у фильтров — фасетные: каждый фильтр считается с учётом
    # ВСЕХ остальных выбранных, но без себя самого (правка Дмитрия
    # 25.08.2026: «выбрал систему и мужчин — в скобках у возрастной группы
    # число не пересчиталось»). Так число у варианта честно обещает, сколько
    # строк останется, если по нему щёлкнуть, а сам фильтр не схлопывается в
    # один вариант и из него всегда можно выйти.
    platforms = _platform_breakdown(
        _facet_rows(all_rows, gender=scope_gender, age_group=scope_age_group)
    )
    # Разбивка по полу — она же цифры плитки «финишёров»: плитка показывает
    # М/Ж, поэтому по полу и НЕ сужается (указание Дмитрия 25.08.2026), а по
    # системе и возрастной группе — да.
    gender_rows = _facet_rows(
        all_rows, platform=scope_platform, age_group=scope_age_group
    )
    gender_summary = _summary(gender_rows)
    gender_counts = {
        "male": gender_summary["male"],
        "female": gender_summary["female"],
        "unknown": gender_summary["unknown_gender"],
        "total": gender_summary["finishers"],
    }
    age_groups = _age_group_breakdown(
        _facet_rows(all_rows, platform=scope_platform, gender=scope_gender)
    )

    # Плитки описывают выбранный зачёт целиком — включая лучшее время и
    # медиану внутри среза.
    summary = _summary(in_scope)
    summary["skipped_foreign_parkrun"] = week.get("skipped_foreign_parkrun", 0)
    # Волонтёры живут при СТАРТЕ, а не при результате: ни времени, ни пола, ни
    # возрастной группы у волонтёрства нет. Поэтому цифра всегда по зачёту
    # системы и не сужается фильтрами — иначе она врала бы, обнуляясь на любом
    # срезе. На странице это сказано подсказкой.
    volunteers = week.get("volunteers") or {}
    scoped_volunteers = (
        [volunteers.get(scope_platform) or {}]
        if scope_platform is not None
        else list(volunteers.values())
    )
    summary["volunteers"] = sum(int(item.get("entries", 0)) for item in scoped_volunteers)
    summary["volunteer_people"] = sum(int(item.get("people", 0)) for item in scoped_volunteers)
    # Сколько строк в самом зачёте — знаменатель для долей «N% финишёров» у
    # новичков и рекордов. Держим отдельно, потому что в плитке «финишёров»
    # ниже стоит цифра фасета, а не среза.
    summary["scope_finishers"] = len(in_scope)
    # …а финишёры в плитке — из фасета пола: цифра с разбивкой М/Ж не должна
    # схлопываться, когда открыт один пол.
    summary["finishers"] = gender_counts["total"]
    summary["male"] = gender_counts["male"]
    summary["female"] = gender_counts["female"]
    summary["unknown_gender"] = gender_counts["unknown"]

    needle = (query or "").strip().lower()
    filtered = [row for row in in_scope if not needle or _matches_query(row, needle)]

    per_page = max(1, min(per_page, MAX_PAGE_SIZE))
    total = len(filtered)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    start = (page - 1) * per_page

    saturdays = [item["saturday"] for item in list_protocol_weeks(db)]
    current = week["saturday"]
    position = bisect_left(saturdays, current)
    previous_saturday = saturdays[position - 1] if position > 0 else None
    next_index = position + 1 if position < len(saturdays) and saturdays[position] == current else position
    next_saturday = saturdays[next_index] if next_index < len(saturdays) else None

    return {
        "week_start": week["week_start"],
        "week_end": week["week_end"],
        "saturday": week["saturday"],
        "scope_platform": scope_platform,
        "gender": gender if gender in GENDER_FILTERS else None,
        "age_group": age_group or None,
        "query": query or None,
        "platforms": platforms,
        "summary": summary,
        "gender_counts": gender_counts,
        "age_groups": age_groups,
        "results": filtered[start : start + per_page],
        "my_results": my_results,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "total": total,
        "previous_saturday": previous_saturday,
        "next_saturday": next_saturday,
        "latest_saturday": saturdays[-1] if saturdays else None,
    }
