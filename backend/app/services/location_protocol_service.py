"""Протокол одного старта на нашем сайте.

Замыкает связку «локация → журнал протоколов → сам протокол»: чтобы посмотреть
состав забега, больше не нужно уходить на 5verst.ru / s95.ru / runpark.ru, а по
русским parkrun-площадкам (01.03.2014–26.02.2022) наш протокол вообще
единственный доступный — свои страницы parkrun по России закрыл.

Что показываем сверх сырого протокола платформы (за образец взята панель
«Протокол забега» дашборда Grafana «Статистика по локациям»):

- место внутри своей возрастной категории («3 / 27») — 5 вёрст и RunPark
  печатают категорию в протоколе, значит место в ней считается;
- место среди своего пола в этом протоколе;
- место результата в истории площадки среди своего пола — сквозь ВСЕ системы
  идентичности, как и «рекорд трассы» в журнале;
- age grade parkrun (в run_results.age_category у parkrun лежит именно он, а не
  категория — см. gender_position_service);
- отметки протокола: дебют в системе, первый раз на этой площадке, личный
  рекорд, рекорд площадки;
- разбивка по возрастным группам и полу, клубы, волонтёры с ролями.

Сводные цифры старта и флаги рекордов не пересчитываем — берём из журнала
локации (build_location_events, кэш 3ч): там уже посчитана и прогрессия
рекордов, и сквозной номер сбора площадки.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import date
from typing import Any
from uuid import UUID

import redis
from sqlalchemy.orm import Session

from app.activity_url import resolve_activity_url
from app.core.redis_client import get_redis_client
from app.models import (
    Event,
    EventCrosslink,
    EventSummary,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services.gender_position_service import (
    GENDER_FEMALE,
    GENDER_MALE,
    gender_from_age_category,
)
from app.services.location_page_service import (
    _age_group_sort_key,
    _dedupe_crosslinked_events,
    _gender_expression,
    _platform_link_join,
    _read_json_cache,
    _write_json_cache,
    build_location_events,
    normalize_age_group,
    resolve_location_identity,
)
from app.time_format import format_finish_time_display, normalize_finish_time_display
from app.volunteer_role_taxonomy import canonical_volunteer_role

# Тот же TTL, что у журнала: субботним вечером протокол дозаливается частями
# (сводка приходит раньше полного состава), и залипший на полдня кэш показывал
# бы «протокола нет» уже после того, как синк его привёз. Для старых стартов
# перерасчёт раз в 3 часа тоже не страшен — их открывают редко.
PROTOCOL_CACHE_TTL_SECONDS = 3 * 60 * 60

# Потолок на подсчёт «какое это место в истории площадки»: на площадке с
# 40 тыс. результатов выборка времён занимает доли секунды, но упереться в
# гипотетического монстра на 300 тыс. строк ради одной колонки не стоит.
HISTORY_RANK_MAX_RESULTS = 150_000

# Сколько клубов показываем в сводке.
CLUBS_TOP_LIMIT = 10


def location_protocol_cache_key(slug: str, platform_code: str, event_date: date) -> str:
    return f"locations:protocol:v3:{slug.strip().lower()}:{platform_code}:{event_date.isoformat()}"


def invalidate_location_protocol_cache(slug: str, platform_code: str, event_date: date) -> None:
    try:
        get_redis_client().delete(location_protocol_cache_key(slug, platform_code, event_date))
    except redis.RedisError:
        pass


def build_location_protocol(
    db: Session,
    slug: str,
    platform_code: str,
    event_date: date,
    *,
    viewer: User | None = None,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any] | None:
    cache_key = location_protocol_cache_key(slug, platform_code, event_date)
    payload: dict[str, Any] | None = None
    if use_cache and not refresh:
        payload = _read_json_cache(cache_key)  # type: ignore[assignment]

    if payload is None:
        payload = _compute_location_protocol(db, slug, platform_code, event_date)
        if use_cache and payload is not None:
            _write_json_cache(cache_key, payload, PROTOCOL_CACHE_TTL_SECONDS)

    if payload is None:
        return None
    # Своя строка подсвечивается ПОСЛЕ кэша: протокол один на всех, а «я» у
    # каждого своё.
    _mark_viewer_rows(db, payload, viewer)
    return payload


def _mark_viewer_rows(db: Session, payload: dict[str, Any], viewer: User | None) -> None:
    rows = payload.get("results") or []
    volunteers = payload.get("volunteers") or []
    for row in rows:
        row["is_me"] = False
    for row in volunteers:
        row["is_me"] = False
    if viewer is None:
        return
    platform_code = payload.get("platform_code")
    if not platform_code:
        return
    own_keys = {
        external_user_id
        for (external_user_id,) in db.query(PlatformLink.external_user_id)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == viewer.id, Platform.code == platform_code)
        .all()
        if external_user_id
    }
    if not own_keys:
        return
    for row in rows:
        row["is_me"] = row.get("external_user_id") in own_keys
    for row in volunteers:
        row["is_me"] = row.get("external_user_id") in own_keys


def _resolve_event(
    db: Session, location_ids: list[UUID], platform_code: str, event_date: date
) -> tuple[Event, str, Location] | None:
    """Событие идентичности по (система, дата).

    Вторичное событие кросслинка (один физический старт, попавший в два
    протокола) подменяем первичным: журнал показывает только первичное, и
    ссылки должны вести туда же.
    """
    rows = (
        db.query(Event, Platform.code, Location)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            Event.location_id.in_(location_ids),
            Event.event_date == event_date,
            Platform.code == platform_code,
            Event.is_test_event.is_(False),
        )
        .all()
    )
    if not rows:
        return None
    kept = _dedupe_crosslinked_events(db, [event.id for event, _code, _loc in rows])
    for event, code, location in rows:
        if event.id in kept:
            return event, code, location

    # Все найденные события — вторичные: уводим на первичное.
    primary_ids = [
        primary_id
        for (primary_id,) in db.query(EventCrosslink.primary_event_id)
        .filter(EventCrosslink.secondary_event_id.in_([event.id for event, _c, _l in rows]))
        .all()
    ]
    if primary_ids:
        primary = (
            db.query(Event, Platform.code, Location)
            .join(Platform, Event.platform_id == Platform.id)
            .join(Location, Event.location_id == Location.id)
            .filter(Event.id.in_(primary_ids))
            .first()
        )
        if primary is not None:
            return primary
    return None


def _compute_location_protocol(
    db: Session, slug: str, platform_code: str, event_date: date
) -> dict[str, Any] | None:
    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None
    location_ids = [location.id for location, _code in identity.locations]

    resolved = _resolve_event(db, location_ids, platform_code, event_date)
    if resolved is None:
        return None
    event, event_platform_code, location = resolved

    journal = build_location_events(db, identity.slug) or {}
    journal_items: list[dict[str, Any]] = list(journal.get("items") or [])  # новые сверху
    # Журнал отсортирован по убыванию даты; для «предыдущий/следующий старт»
    # удобнее хронология.
    chronology = sorted(journal_items, key=lambda item: (str(item["event_date"]), str(item["platform_code"])))
    index = next(
        (
            position
            for position, item in enumerate(chronology)
            if str(item["platform_code"]) == event_platform_code
            and str(item["event_date"]) == event.event_date.isoformat()
        ),
        None,
    )
    journal_row: dict[str, Any] = chronology[index] if index is not None else {}
    previous_item = chronology[index - 1] if index is not None and index > 0 else None
    next_item = chronology[index + 1] if index is not None and index + 1 < len(chronology) else None

    results, gender_counts, club_counts = _build_results(db, event, event_platform_code)
    _attach_history_ranks(db, location_ids, results)
    volunteers = _build_volunteers(db, event, location_ids)

    summary_row = (
        db.query(EventSummary)
        .filter(EventSummary.event_id == event.id)
        .first()
    )
    declared_finishers = event.finishers_count or (summary_row.finishers_count if summary_row else None)
    max_position = max((row["position"] for row in results if row["position"] is not None), default=0)
    # Протокол считаем неполным, если в нём меньше строк, чем позиций или чем
    # заявлено финишёров. Так честно помечаются зарубежные parkrun-старты, где в
    # БД лежат только строки наших участников, вытянутые из их профилей.
    is_partial = bool(results) and (
        max_position > len(results) or (declared_finishers or 0) > len(results)
    )

    times = sorted(row["finish_time_sec"] for row in results if row["finish_time_sec"])
    age_groups = _age_group_breakdown(results)

    payload: dict[str, Any] = {
        "slug": identity.slug,
        "name": identity.name,
        "city": location.city,
        "platform_code": event_platform_code,
        "event_date": event.event_date,
        "event_number": event.event_number,
        "overall_number": journal_row.get("overall_number"),
        "title": event.title,
        "source_url": resolve_activity_url(
            platform_code=event_platform_code,
            event_date=event.event_date,
            event_number=event.event_number,
            event_source_url=event.source_url,
            location_external_key=location.external_key,
            summary_source_url=summary_row.source_url if summary_row else None,
        ),
        "has_protocol": bool(results),
        "is_partial": is_partial,
        "declared_finishers": declared_finishers,
        "previous": _neighbour(previous_item),
        "next": _neighbour(next_item),
        "summary": {
            "finishers": len(results),
            "volunteers": len(volunteers),
            "male": gender_counts.get(GENDER_MALE, 0),
            "female": gender_counts.get(GENDER_FEMALE, 0),
            "unknown_gender": gender_counts.get(None, 0),
            "avg_time_sec": round(sum(times) / len(times)) if times else None,
            "avg_time_display": _fmt(round(sum(times) / len(times))) if times else None,
            "median_time_sec": _median(times),
            "median_time_display": _fmt(_median(times)),
            "best_time_sec": times[0] if times else None,
            "best_time_display": _fmt(times[0] if times else None),
            "last_time_sec": times[-1] if times else None,
            "last_time_display": _fmt(times[-1] if times else None),
            "best_male_time_display": _best_display(results, GENDER_MALE),
            "best_male_runner_name": _best_name(results, GENDER_MALE),
            "best_female_time_display": _best_display(results, GENDER_FEMALE),
            "best_female_runner_name": _best_name(results, GENDER_FEMALE),
            "debutants": sum(1 for row in results if row["is_first_run"]),
            "first_at_location": sum(1 for row in results if row["is_first_run_at_location"]),
            "prs": sum(1 for row in results if row["is_pr"]),
            "location_prs": sum(1 for row in results if row["is_location_pr"]),
            "clubs_count": len(club_counts),
            "top_clubs": [
                {"name": name, "count": count}
                for name, count in sorted(club_counts.items(), key=lambda pair: (-pair[1], pair[0]))[
                    :CLUBS_TOP_LIMIT
                ]
            ],
            "is_attendance_record": bool(journal_row.get("is_attendance_record")),
            "is_course_record_male": bool(journal_row.get("is_course_record_male")),
            "is_course_record_female": bool(journal_row.get("is_course_record_female")),
        },
        "age_groups": age_groups,
        "results": results,
        "volunteers": volunteers,
    }
    return payload


def _neighbour(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "platform_code": item.get("platform_code"),
        "event_date": item.get("event_date"),
        "event_number": item.get("event_number"),
        "overall_number": item.get("overall_number"),
    }


def _fmt(value: int | None) -> str | None:
    return format_finish_time_display(value) if value else None


def _median(times: list[int]) -> int | None:
    if not times:
        return None
    middle = len(times) // 2
    if len(times) % 2:
        return times[middle]
    return round((times[middle - 1] + times[middle]) / 2)


def _best_row(results: list[dict[str, Any]], gender: str) -> dict[str, Any] | None:
    candidates = [
        row for row in results if row["gender"] == gender and row["finish_time_sec"]
    ]
    return min(candidates, key=lambda row: row["finish_time_sec"]) if candidates else None


def _best_display(results: list[dict[str, Any]], gender: str) -> str | None:
    row = _best_row(results, gender)
    return row["finish_time_display"] if row else None


def _best_name(results: list[dict[str, Any]], gender: str) -> str | None:
    row = _best_row(results, gender)
    return row["name"] if row else None


def _row_gender(
    platform_code: str,
    age_category: str | None,
    participant_gender: str | None,
    participant_age_category: str | None,
) -> str | None:
    """Пол финишёра — те же источники, что у gender_position_service.

    Сначала категория самого протокола (5 вёрст «М35-39», RunPark «VM35-39»),
    затем материализованный participants.gender (он же закрывает s95), и только
    потом категория профиля (parkrun «SM30-34»).
    """
    direct = gender_from_age_category(platform_code, age_category)
    if direct:
        return direct
    if participant_gender in (GENDER_MALE, GENDER_FEMALE):
        return participant_gender
    return gender_from_age_category("runpark", participant_age_category)


def _age_grade(age_category: str | None) -> float | None:
    """Age grade parkrun из run_results.age_category («70.13%» → 70.13)."""
    if not age_category:
        return None
    cleaned = age_category.strip()
    if not cleaned.endswith("%"):
        return None
    try:
        return round(float(cleaned[:-1].replace(",", ".")), 2)
    except ValueError:
        return None


def _build_results(
    db: Session, event: Event, platform_code: str
) -> tuple[list[dict[str, Any]], dict[str | None, int], dict[str, int]]:
    """Строки протокола с местами по полу и возрастной категории."""
    # serial_id только у публичных профилей: приватный отдаёт 403 всем, кроме
    # владельца, — ссылка вела бы в тупик (та же логика, что в
    # leaderboard_service._site_links).
    rows = (
        db.query(
            RunResult,
            Participant.display_name,
            Participant.external_user_id,
            Participant.profile_url,
            Participant.gender,
            Participant.age_category.label("participant_age_category"),
            Participant.club_name.label("participant_club"),
            User.serial_id,
            User.profile_private,
        )
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .outerjoin(User, PlatformLink.user_id == User.id)
        .filter(RunResult.event_id == event.id)
        .order_by(
            RunResult.position.asc().nulls_last(),
            RunResult.finish_time_sec.asc().nulls_last(),
        )
        .all()
    )

    results: list[dict[str, Any]] = []
    seen: set[UUID] = set()
    gender_counts: dict[str | None, int] = defaultdict(int)
    club_counts: dict[str, int] = defaultdict(int)

    for row in rows:
        result: RunResult = row[0]
        # Один участник может быть привязан к нескольким аккаунтам сайта —
        # join к platform_links размножает строку протокола.
        if result.id in seen:
            continue
        seen.add(result.id)

        raw_category = (result.age_category or "").strip() or None
        # «НЕИЗВЕСТНЫЙ» — финишёр без штрихкода: 5 вёрст помечает статусом
        # unknown, s95 — unknown_runner, у части систем остаётся только имя.
        display_name = (row.display_name or "").strip()
        is_unknown = (
            result.participant_id is None
            or (result.status or "").strip().lower() in ("unknown", "unknown_runner")
            or display_name.lower() in ("неизвестный", "unknown")
        )
        age_group = normalize_age_group(raw_category)
        gender = _row_gender(platform_code, raw_category, row.gender, row.participant_age_category)
        club = (result.club_name or row.participant_club or "").strip() or None
        finish_time_sec = result.finish_time_sec if result.finish_time_sec else None

        gender_counts[gender] += 1
        if club:
            club_counts[club] += 1

        results.append(
            {
                "position": result.position,
                "name": row.display_name,
                "external_user_id": row.external_user_id,
                "profile_url": row.profile_url,
                "serial_id": row.serial_id if row.profile_private is False else None,
                "gender": gender,
                "gender_position": result.gender_position,
                "gender_total": None,
                # Категория показывается ровно в том виде, в каком стоит в
                # протоколе («М35-39»); age_group — та же категория, сведённая к
                # общему для систем виду («35–44»), для гистограммы.
                "age_category": raw_category if age_group else None,
                "age_group": age_group,
                "age_group_position": None,
                "age_group_total": None,
                "age_grade": _age_grade(raw_category),
                "finish_time_sec": finish_time_sec,
                "finish_time_display": normalize_finish_time_display(
                    finish_time_sec, result.finish_time_display
                ),
                "pace_display": result.pace_display,
                "club_name": club,
                "status": result.status,
                "is_unknown": is_unknown,
                "is_pr": bool(result.is_pr),
                "is_global_pr": bool(result.is_global_pr),
                "is_location_pr": bool(result.is_location_pr),
                "is_first_run": bool(result.is_first_run),
                "is_first_run_at_location": bool(result.is_first_run_at_location),
                "achievement_labels": list(result.achievement_labels or []),
                "history_rank": None,
                "history_total": None,
            }
        )

    _fill_gender_places(results)
    _fill_age_group_places(results)
    return results, dict(gender_counts), dict(club_counts)


def _fill_gender_places(results: list[dict[str, Any]]) -> None:
    """Место среди своего пола в этом протоколе.

    Место из протокола платформы (gender_position) считаем главнее своего
    счёта: в неполном протоколе — например у зарубежного parkrun, где в БД
    лежат только строки наших участников, — пересчёт по имеющимся строкам дал
    бы «1-я среди женщин» каждой женщине. Своё место считаем лишь там, где
    платформа его не дала (5 вёрст до бэкфилла, RunPark без категории).
    """
    by_gender: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        if row["gender"]:
            by_gender[row["gender"]].append(row)

    for gender_rows in by_gender.values():
        ranked = sorted(
            (row for row in gender_rows if row["finish_time_sec"]),
            key=lambda row: (row["finish_time_sec"], row["position"] or 0),
        )
        for place, row in enumerate(ranked, start=1):
            if row["gender_position"] is None:
                row["gender_position"] = place
        # Всего в зачёте пола: строки протокола либо, если протокол неполный и
        # платформа дала места дальше нашего счёта, максимум из её мест.
        total = max(
            len(gender_rows),
            max((row["gender_position"] or 0) for row in gender_rows),
        )
        for row in gender_rows:
            row["gender_total"] = total


def _fill_age_group_places(results: list[dict[str, Any]]) -> None:
    """Место внутри своей возрастной категории — как в панели Grafana.

    Партиционируем по СЫРОЙ категории протокола («М35-39»), а не по сведённой
    группе: категория уже содержит пол, и ровно она напечатана в протоколе.
    """
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        if row["age_category"]:
            by_category[row["age_category"]].append(row)

    for category_rows in by_category.values():
        ranked = sorted(
            (row for row in category_rows if row["finish_time_sec"]),
            key=lambda row: (row["finish_time_sec"], row["position"] or 0),
        )
        for place, row in enumerate(ranked, start=1):
            row["age_group_position"] = place
        for row in category_rows:
            row["age_group_total"] = len(category_rows)


def _age_group_breakdown(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Разбивка старта по возрастным группам — М/Ж/без данных в каждой ступени."""
    buckets: dict[str, dict[str, int]] = {}
    for row in results:
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


def _volunteer_history(
    db: Session, event: Event, location_ids: list[UUID], participant_ids: list[UUID]
) -> tuple[dict[UUID, int], dict[UUID, int], dict[UUID, set[str]]]:
    """История волонтёрств ДО этого старта: сколько всего, сколько здесь, какие роли.

    Ровно панель «Детали по волонтёрам» дашборда Grafana: по ней видно, у кого
    это первое волонтёрство, кто впервые волонтёрит на этой площадке и кто
    осваивает новую роль. Тестовые старты исключены, как во всей витрине.
    """
    if not participant_ids:
        return {}, {}, {}

    base = (
        db.query(VolunteerResult.participant_id, Event.location_id, VolunteerResult.role)
        .join(Event, VolunteerResult.event_id == Event.id)
        .filter(
            VolunteerResult.participant_id.in_(participant_ids),
            Event.event_date < event.event_date,
            Event.is_test_event.is_(False),
        )
        .all()
    )
    career: dict[UUID, int] = defaultdict(int)
    here: dict[UUID, int] = defaultdict(int)
    prior_roles: dict[UUID, set[str]] = defaultdict(set)
    location_set = set(location_ids)
    for participant_id, location_id, raw_role in base:
        career[participant_id] += 1
        if location_id in location_set:
            here[participant_id] += 1
        role = canonical_volunteer_role(raw_role)
        if role is not None:
            prior_roles[participant_id].add(role.key)
    return dict(career), dict(here), dict(prior_roles)


def _build_volunteers(db: Session, event: Event, location_ids: list[UUID]) -> list[dict[str, Any]]:
    """Волонтёры старта: один человек — одна строка, роли собраны в список."""
    rows = (
        db.query(
            VolunteerResult.role,
            VolunteerResult.participant_id,
            VolunteerResult.external_result_key,
            Participant.display_name,
            Participant.external_user_id,
            Participant.profile_url,
            User.serial_id,
            User.profile_private,
        )
        .outerjoin(Participant, VolunteerResult.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .outerjoin(User, PlatformLink.user_id == User.id)
        .filter(VolunteerResult.event_id == event.id)
        .all()
    )

    career, here, prior_roles = _volunteer_history(
        db,
        event,
        location_ids,
        [row.participant_id for row in rows if row.participant_id is not None],
    )

    people: dict[str, dict[str, Any]] = {}
    seen_role_keys: set[tuple[str, str]] = set()
    for row in rows:
        key = str(row.participant_id or row.external_result_key)
        career_before = career.get(row.participant_id, 0) if row.participant_id else 0
        person = people.setdefault(
            key,
            {
                "name": row.display_name,
                "external_user_id": row.external_user_id,
                "profile_url": row.profile_url,
                "serial_id": row.serial_id if row.profile_private is False else None,
                "roles": [],
                "new_roles": [],
                # Какое это волонтёрство по счёту в карьере человека (в системе).
                "volunteer_number": career_before + 1 if row.participant_id else None,
                "is_first_volunteering": row.participant_id is not None and career_before == 0,
                "is_first_here": row.participant_id is not None
                and here.get(row.participant_id, 0) == 0,
            },
        )
        role = canonical_volunteer_role(row.role)
        if role is None:
            continue
        # join к platform_links размножает строки так же, как в протоколе.
        if (key, role.key) in seen_role_keys:
            continue
        seen_role_keys.add((key, role.key))
        person["roles"].append(role.label)
        if row.participant_id is not None and role.key not in prior_roles.get(
            row.participant_id, set()
        ):
            person["new_roles"].append(role.label)

    for person in people.values():
        person["roles"].sort()
        person["new_roles"].sort()
    # Без ролей человек в списке не нужен: у parkrun такую строку даёт служебная
    # сводка «Total Credits (N×)», ролью не являющаяся.
    return sorted(
        (person for person in people.values() if person["roles"]),
        key=lambda person: (person["name"] or "").lower(),
    )


def _attach_history_ranks(
    db: Session, location_ids: list[UUID], results: list[dict[str, Any]]
) -> None:
    """«Какое это место в истории площадки среди своего пола».

    Считаем сквозь ВСЕ системы идентичности — ровно как «рекорд трассы» в
    журнале: трасса одна, и результат 2016 года на parkrun сравним с 2026-м на
    5 вёрстах. Тестовые старты и вторичные события кросслинков исключены, как
    везде в витрине локации.
    """
    if not any(row["gender"] and row["finish_time_sec"] for row in results):
        return

    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    history_query = (
        db.query(gender_expr.label("gender"), RunResult.finish_time_sec)
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .filter(
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
            Event.id.notin_(db.query(EventCrosslink.secondary_event_id)),
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
        )
    )
    if history_query.count() > HISTORY_RANK_MAX_RESULTS:
        return

    times_by_gender: dict[str, list[int]] = defaultdict(list)
    for gender, finish_time_sec in history_query.all():
        if gender in (GENDER_MALE, GENDER_FEMALE):
            times_by_gender[gender].append(int(finish_time_sec))
    for times in times_by_gender.values():
        times.sort()

    for row in results:
        gender = row["gender"]
        finish_time_sec = row["finish_time_sec"]
        if not gender or not finish_time_sec:
            continue
        times = times_by_gender.get(gender)
        if not times:
            continue
        row["history_rank"] = bisect_left(times, finish_time_sec) + 1
        row["history_total"] = len(times)
