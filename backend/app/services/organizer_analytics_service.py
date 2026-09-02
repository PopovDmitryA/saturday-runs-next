"""Аналитика локации для кабинета организатора.

Четыре среза, которых не было ни в Grafana, ни на сайте (заказ Дмитрия
18.08.2026):

* нагрузка на команду и bus-фактор ролей — кто выгорит первым;
* динамика посещаемости — растём или падаем;
* портрет участника — кто к нам ходит (возраст, пол, клубы);
* сравнение с соседями — как мы на фоне других локаций системы.

Все тяжёлые агрегаты кэшируются в Redis: данные меняются раз в неделю после
субботнего синка, точечная инвалидация не окупается (тот же подход, что у
страниц локаций).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Event,
    EventCrosslink,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    VolunteerResult,
)
from app.services.location_page_service import (
    LocationIdentity,
    _location_event_ids,
    _platform_link_join,
    _read_json_cache,
    _write_json_cache,
)
from app.services.organizer_access_service import ORGANIZER_ROLE_KEY
from app.volunteer_role_taxonomy import canonical_volunteer_role, strip_role_counters

ANALYTICS_CACHE_TTL_SECONDS = 3 * 60 * 60
# Сетевые срезы считаются по всем локациям системы — держим сутки.
NETWORK_CACHE_TTL_SECONDS = 24 * 60 * 60

DEFAULT_MONTHS = 12

# Место в возрастной группе протокол 5 вёрст пишет прямо в категорию
# («М40-44 (2)») — для портрета участника нужна чистая группа.
_AGE_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")

# Ключевые роли: их отсутствие срывает старт, поэтому bus-фактор считаем по ним
# (тот же список, что в шаблоне «Нужны волонтёры»).
# Роли, без которых старт не состоится. Сужено 24.08.2026 (решение Дмитрия):
# маршалы, замыкающий, подготовка и обработка результатов — важные, но
# заменимые на месте; ключ 🔑 оставлен только за по-настоящему блокирующими.
CRITICAL_ROLE_KEYS: frozenset[str] = frozenset(
    {
        "run_director",
        "timekeeper",
        "barcode_scanning",
        "finish_tokens",
    }
)


def _period_start(months: int) -> date:
    # 0 — «текущий календарный год»: с 1 января (просьба Дмитрия 24.08.2026).
    if months == 0:
        return date(date.today().year, 1, 1)
    return date.today() - timedelta(days=30 * months)


def _clean_age_group(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _AGE_SUFFIX_RE.sub("", value).strip()
    return cleaned or None


# ===== Нагрузка на команду и bus-фактор =====


def team_load_cache_key(identity_key: str, months: int) -> str:
    # v2 — ярлыки ролей как в системе, ключевых ролей меньше.
    return f"organizer:team:v2:{identity_key}:{months}"


def build_team_load(
    db: Session,
    identity: LocationIdentity,
    *,
    months: int = DEFAULT_MONTHS,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = team_load_cache_key(identity.identity_key, months)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached
    payload = _compute_team_load(db, identity, months=months)
    if use_cache:
        _write_json_cache(cache_key, payload, ANALYTICS_CACHE_TTL_SECONDS)
    return payload


def _bus_factor(shares: list[int]) -> int:
    """Сколько человек закрывают 80% волонтёрств роли: 1 — держится на одном."""
    total = sum(shares)
    if total == 0:
        return 0
    running = 0
    for index, count in enumerate(sorted(shares, reverse=True), start=1):
        running += count
        if running / total >= 0.8:
            return index
    return len(shares)


def _director_rotation(
    role_people: dict[str, dict[Any, int]],
    person_names: dict[Any, tuple[str | None, str | None]],
    months: int,
) -> dict[str, Any] | None:
    """Светофор ротации организаторов: сколько людей ведут старт и не держится
    ли он на одном человеке.

    Отдельная метрика, а не строка в общей таблице ролей: организатор — та роль,
    выгорание в которой закрывает площадку целиком (просьба Дмитрия 03.09.2026).

    Пороги не выдуманы, а взяты из распределения по стране. На снимке прода
    250 площадок с 10+ стартами за год: разных организаторов медиана 9
    (10-й перцентиль 4), доля самого частого медиана 32% (75-й — 47%, 90-й — 67%).
    Отсюда:
      зелёный  — доля ≤ 40% и людей ≥ 4: так живут две трети площадок;
      жёлтый   — 41-70% либо людей 2-3: хвост между 75-м и 90-м перцентилем;
      красный  — больше 70% либо один человек: худшие 7% (17 площадок из 250).
    """
    people = role_people.get(ORGANIZER_ROLE_KEY)
    if not people:
        return None
    slots = sum(people.values())
    if not slots:
        return None
    top_pid = max(people, key=lambda pid: people[pid])
    top_count = people[top_pid]
    top_share = round(top_count / slots * 100)
    count = len(people)

    if count <= 1 or top_share > 70:
        level = "red"
    elif count <= 3 or top_share > 40:
        level = "yellow"
    else:
        level = "green"

    return {
        "months": months,
        "slots": slots,
        "people": count,
        "top_name": person_names.get(top_pid, (None, None))[0],
        "top_count": top_count,
        "top_share_pct": top_share,
        "level": level,
    }


def _compute_team_load(
    db: Session, identity: LocationIdentity, *, months: int
) -> dict[str, Any]:
    location_ids = [location.id for location, _code in identity.locations]
    event_ids = _location_event_ids(db, location_ids)
    since = _period_start(months)

    base: dict[str, Any] = {
        "location": {"slug": identity.slug, "name": identity.name},
        "months": months,
        "events_total": 0,
        "volunteers_total": 0,
        "slots_total": 0,
        "avg_per_event": None,
        "top_load": [],
        "roles": [],
        "director_rotation": None,
        "network_note": None,
    }
    if not event_ids:
        return base

    rows = (
        db.query(
            VolunteerResult.participant_id,
            VolunteerResult.role,
            VolunteerResult.event_id,
            Participant.display_name,
            Participant.profile_url,
            Event.event_date,
        )
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .filter(VolunteerResult.event_id.in_(event_ids), Event.event_date >= since)
        .all()
    )
    if not rows:
        return base

    events_in_period = {
        row[0]
        for row in db.query(Event.id)
        .filter(Event.id.in_(event_ids), Event.event_date >= since)
        .all()
    }
    role_people: dict[str, dict[Any, int]] = {}
    role_labels: dict[str, str] = {}
    person_slots: dict[Any, int] = {}
    person_names: dict[Any, tuple[str | None, str | None]] = {}
    # Волонтёрства по дням: считаем именно волонтёрства (роль на старте), а не
    # дни, иначе
    # «из 128 волонтёрств чистых 51» сравнивало бы строки с датами — у человека,
    # берущего по две роли за старт, число падало бы вдвое просто так.
    vol_slots_by_day: dict[Any, dict[date, int]] = {}
    for pid, role, _event_id, name, profile_url, event_date in rows:
        by_day = vol_slots_by_day.setdefault(pid, {})
        by_day[event_date] = by_day.get(event_date, 0) + 1
        canonical = canonical_volunteer_role(role)
        if canonical is None:
            continue
        # Ярлык — как в системе (у 5в «Организатор», не «Директор забега»):
        # канонический ключ группирует, показываем сырое название без счётчиков.
        role_labels.setdefault(canonical.key, strip_role_counters(role))
        role_people.setdefault(canonical.key, {})
        role_people[canonical.key][pid] = role_people[canonical.key].get(pid, 0) + 1
        person_slots[pid] = person_slots.get(pid, 0) + 1
        person_names[pid] = (name, profile_url)

    # Пробежки тех же людей: сколько раз бегали ЗДЕСЬ (это баланс «бегает или
    # только помогает») и в какие дни бегали ГДЕ УГОДНО. Второе нужно фильтру
    # «только чистые волонтёрства»: если человек бежал в Пестовском, а волонтёрил
    # в тот же день в Мещерском, для Мещерского это волонтёрство не чистое
    # (Дмитрий 03.09.2026). Ключ группировки — профиль сайта, иначе пробежка в
    # соседней системе не нашлась бы.
    participant_ids = list(person_slots)
    group_of: dict[Any, Any] = {}
    for pid, gkey in (
        db.query(Participant.id, func.coalesce(PlatformLink.user_id, Participant.id))
        .outerjoin(PlatformLink, _platform_link_join())
        .filter(Participant.id.in_(participant_ids))
        .all()
    ):
        group_of[pid] = gkey

    runs_here: dict[Any, int] = {}
    for pid, count in (
        db.query(RunResult.participant_id, func.count(func.distinct(RunResult.event_id)))
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id.in_(participant_ids),
            RunResult.event_id.in_(event_ids),
            Event.event_date >= since,
        )
        .group_by(RunResult.participant_id)
        .all()
    ):
        runs_here[pid] = int(count)

    run_dates_by_group: dict[Any, set[date]] = {}
    if group_of:
        group_key = func.coalesce(PlatformLink.user_id, RunResult.participant_id)
        for gkey, event_date in (
            db.query(group_key.label("gkey"), Event.event_date)
            .select_from(RunResult)
            .join(Event, RunResult.event_id == Event.id)
            .join(Participant, RunResult.participant_id == Participant.id)
            .outerjoin(PlatformLink, _platform_link_join())
            .filter(
                group_key.in_(set(group_of.values())),
                Event.event_date >= since,
                Event.is_test_event.is_(False),
            )
            .distinct()
            .all()
        ):
            run_dates_by_group.setdefault(gkey, set()).add(event_date)

    director_rotation = _director_rotation(role_people, person_names, months)

    network = network_role_rotation(db, identity, months=months)

    roles: list[dict[str, Any]] = []
    for key, people in role_people.items():
        shares = list(people.values())
        slots = sum(shares)
        top_pid = max(people, key=lambda pid: people[pid])
        top_count = people[top_pid]
        rotation = round(len(people) / slots * 100) if slots else 0
        network_rotation = network.get(key)
        roles.append(
            {
                "role_key": key,
                "role": role_labels[key],
                "is_critical": key in CRITICAL_ROLE_KEYS,
                "slots": slots,
                "people": len(people),
                "bus_factor": _bus_factor(shares),
                "top_name": person_names.get(top_pid, (None, None))[0],
                "top_count": top_count,
                "top_share_pct": round(top_count / slots * 100) if slots else 0,
                "rotation_pct": rotation,
                "network_rotation_pct": network_rotation,
                "rotation_delta_pct": (
                    rotation - network_rotation if network_rotation is not None else None
                ),
            }
        )

    # Наверху — то, что рискованнее: критичные роли с наименьшим bus-фактором.
    roles.sort(
        key=lambda item: (
            0 if item["is_critical"] else 1,
            item["bus_factor"],
            -item["top_share_pct"],
            item["role"],
        )
    )

    top_load = sorted(
        (
            {
                "participant_id": str(pid),
                "name": person_names.get(pid, (None, None))[0],
                "profile_url": person_names.get(pid, (None, None))[1],
                "slots": count,
                "share_pct": round(count / sum(person_slots.values()) * 100),
                # Пробежки здесь за тот же период — вторая половина баланса.
                "runs_here": runs_here.get(pid, 0),
                # Волонтёрства в дни, когда человек нигде не бежал.
                "pure_slots": sum(
                    slots_that_day
                    for day, slots_that_day in vol_slots_by_day.get(pid, {}).items()
                    if day not in run_dates_by_group.get(group_of.get(pid), set())
                ),
            }
            for pid, count in person_slots.items()
        ),
        key=lambda item: -int(item["slots"]),
    )[:10]

    events_count = len(events_in_period)
    slots_total = sum(person_slots.values())
    base.update(
        {
            "events_total": events_count,
            "volunteers_total": len(person_slots),
            "slots_total": slots_total,
            "avg_per_event": round(slots_total / events_count, 1) if events_count else None,
            "top_load": top_load,
            "roles": roles,
        "director_rotation": director_rotation,
        }
    )
    return base


def network_role_cache_key(platform_code: str, months: int) -> str:
    return f"organizer:network-roles:v1:{platform_code}:{months}"


def network_role_rotation(
    db: Session, identity: LocationIdentity, *, months: int
) -> dict[str, int]:
    """Средняя ротируемость каждой роли по всем локациям системы, %.

    Ротируемость = разных людей / число волонтёрств. Считается по локациям с хотя бы
    пятью волонтёрствами роли, затем усредняется — иначе крошечные локации со 100%
    ротацией задирают планку.
    """
    platform_code = next((code for _loc, code in identity.locations), None)
    if platform_code is None:
        return {}
    cache_key = network_role_cache_key(platform_code, months)
    cached = _read_json_cache(cache_key)
    if cached is not None:
        return {key: int(value) for key, value in cached.items()}

    since = _period_start(months)
    secondary_events = select(EventCrosslink.secondary_event_id)
    # Счётчики сворачиваются в SQL до (локация × очищенный ярлык роли): версия
    # с группировкой по участнику доходила до 4 минут на всю систему. Счётчики
    # parkrun («Marshal (12×)») и вехи С95 срезаются регекспами — SQL-двойник
    # strip_role_counters, иначе каждый ярлык parkrun был бы уникальным.
    cleaned_role = func.trim(
        func.regexp_replace(
            func.regexp_replace(VolunteerResult.role, r"\(\s*\d+\s*[×xX]\s*\)\s*$", ""),
            r"\s+\d+$",
            "",
        )
    )
    rows = (
        db.query(
            Event.location_id,
            cleaned_role.label("role"),
            func.count(func.distinct(VolunteerResult.participant_id)),
            func.count(func.distinct(func.concat(VolunteerResult.event_id, VolunteerResult.participant_id))),
        )
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(
            Platform.code == platform_code,
            Event.event_date >= since,
            Event.is_test_event.is_(False),
            Event.id.notin_(secondary_events),
            VolunteerResult.participant_id.isnot(None),
            VolunteerResult.role.isnot(None),
        )
        .group_by(Event.location_id, cleaned_role)
        .all()
    )
    per_location: dict[tuple[Any, str], dict[str, int]] = {}
    for location_id, role, people, slots in rows:
        canonical = canonical_volunteer_role(role)
        if canonical is None:
            continue
        bucket = per_location.setdefault((location_id, canonical.key), {"people": 0, "slots": 0})
        bucket["people"] += int(people)
        bucket["slots"] += int(slots)

    by_role: dict[str, list[float]] = {}
    for (_location_id, role_key), bucket in per_location.items():
        if bucket["slots"] < 5:
            continue
        by_role.setdefault(role_key, []).append(bucket["people"] / bucket["slots"] * 100)
    result = {key: round(sum(values) / len(values)) for key, values in by_role.items() if values}
    _write_json_cache(cache_key, result, NETWORK_CACHE_TTL_SECONDS)
    return result


# ===== Динамика посещаемости =====


def attendance_cache_key(identity_key: str) -> str:
    # v2 — у событий появилась платформа (эры parkrun/5в на графике).
    return f"organizer:attendance:v2:{identity_key}"


def build_attendance(
    db: Session,
    identity: LocationIdentity,
    *,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = attendance_cache_key(identity.identity_key)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached
    payload = _compute_attendance(db, identity)
    if use_cache:
        _write_json_cache(cache_key, payload, ANALYTICS_CACHE_TTL_SECONDS)
    return payload


def _compute_attendance(db: Session, identity: LocationIdentity) -> dict[str, Any]:
    """Ряд «сколько финишировало на каждом старте» + месячные средние.

    Помесячная агрегация нужна графику: недельный ряд за пять лет — это каша,
    а среднее по месяцу показывает тренд и сезонность.
    """
    location_ids = [location.id for location, _code in identity.locations]
    event_ids = _location_event_ids(db, location_ids)

    base: dict[str, Any] = {
        "location": {"slug": identity.slug, "name": identity.name},
        "events": [],
        "months": [],
        "events_total": 0,
        "last_12m_avg": None,
        "prev_12m_avg": None,
        "yoy_delta_pct": None,
        "record_finishers": None,
        "record_date": None,
    }
    if not event_ids:
        return base

    finishers = (
        select(func.count())
        .select_from(RunResult)
        .where(RunResult.event_id == Event.id)
        .scalar_subquery()
    )
    volunteers = (
        select(func.count())
        .select_from(VolunteerResult)
        .where(VolunteerResult.event_id == Event.id)
        .scalar_subquery()
    )
    rows = (
        db.query(
            Event.event_date,
            Event.event_number,
            Platform.code,
            finishers.label("finishers"),
            volunteers.label("volunteers"),
        )
        .join(Platform, Event.platform_id == Platform.id)
        .filter(Event.id.in_(event_ids))
        .order_by(Event.event_date)
        .all()
    )
    events = [
        {
            "date": event_date.isoformat(),
            "date_display": event_date.strftime("%d.%m.%Y"),
            "event_number": event_number,
            "platform_code": platform_code,
            "finishers": int(finishers_count or 0),
            "volunteers": int(volunteers_count or 0),
        }
        for event_date, event_number, platform_code, finishers_count, volunteers_count in rows
    ]
    # Старты без протокола (0 финишёров) — это отмены и сбои загрузки; в
    # средних они бы занижали картину, поэтому считаем только состоявшиеся.
    held = [item for item in events if item["finishers"] > 0]

    months_map: dict[str, list[dict[str, Any]]] = {}
    for item in held:
        months_map.setdefault(item["date"][:7], []).append(item)
    months = []
    for key, items in sorted(months_map.items()):
        values = [item["finishers"] for item in items]
        # Платформа месяца — по большинству стартов: ей красится колонка графика
        # (фишка страницы — видно эру parkrun до ребрендинга в 5 вёрст).
        platform_counts: dict[str, int] = {}
        for item in items:
            platform_counts[item["platform_code"]] = (
                platform_counts.get(item["platform_code"], 0) + 1
            )
        months.append(
            {
                "month": key,
                "events": len(values),
                "avg_finishers": round(sum(values) / len(values), 1),
                "max_finishers": max(values),
                "platform_code": max(platform_counts, key=lambda code: platform_counts[code]),
            }
        )

    today = date.today()
    year_ago = (today - timedelta(days=365)).isoformat()
    two_years_ago = (today - timedelta(days=730)).isoformat()
    last_12 = [item["finishers"] for item in held if item["date"] >= year_ago]
    prev_12 = [
        item["finishers"] for item in held if two_years_ago <= item["date"] < year_ago
    ]
    last_avg = round(sum(last_12) / len(last_12), 1) if last_12 else None
    prev_avg = round(sum(prev_12) / len(prev_12), 1) if prev_12 else None
    record = max(held, key=lambda item: item["finishers"]) if held else None

    base.update(
        {
            "events": events,
            "months": months,
            "events_total": len(held),
            "last_12m_avg": last_avg,
            "prev_12m_avg": prev_avg,
            "yoy_delta_pct": (
                round((last_avg - prev_avg) / prev_avg * 100)
                if last_avg is not None and prev_avg
                else None
            ),
            "record_finishers": record["finishers"] if record else None,
            "record_date": record["date_display"] if record else None,
        }
    )
    return base


# ===== Портрет участника =====


def audience_cache_key(identity_key: str, months: int) -> str:
    return f"organizer:audience:v1:{identity_key}:{months}"


def build_audience(
    db: Session,
    identity: LocationIdentity,
    *,
    months: int = DEFAULT_MONTHS,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = audience_cache_key(identity.identity_key, months)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached
    payload = _compute_audience(db, identity, months=months)
    if use_cache:
        _write_json_cache(cache_key, payload, ANALYTICS_CACHE_TTL_SECONDS)
    return payload


def _compute_audience(
    db: Session, identity: LocationIdentity, *, months: int
) -> dict[str, Any]:
    """Кто к нам ходит: возрастные группы, пол и клубы за период."""
    location_ids = [location.id for location, _code in identity.locations]
    event_ids = _location_event_ids(db, location_ids)
    since = _period_start(months)

    base: dict[str, Any] = {
        "location": {"slug": identity.slug, "name": identity.name},
        "months": months,
        "finishes_total": 0,
        "people_total": 0,
        "age_groups": [],
        "genders": [],
        "clubs": [],
    }
    if not event_ids:
        return base

    rows = (
        db.query(
            RunResult.participant_id,
            RunResult.age_category,
            Participant.gender,
            Participant.club_name,
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .filter(RunResult.event_id.in_(event_ids), Event.event_date >= since)
        .all()
    )
    if not rows:
        return base

    age_counts: dict[str, int] = {}
    gender_counts: dict[str, int] = {}
    club_people: dict[str, set[Any]] = {}
    club_finishes: dict[str, int] = {}
    people: set[Any] = set()
    for pid, age_category, gender, club_name in rows:
        people.add(pid)
        group = _clean_age_group(age_category)
        if group:
            age_counts[group] = age_counts.get(group, 0) + 1
        key = {"male": "Мужчины", "female": "Женщины"}.get((gender or "").lower())
        if key:
            gender_counts[key] = gender_counts.get(key, 0) + 1
        club = (club_name or "").strip()
        if club:
            club_people.setdefault(club, set()).add(pid)
            club_finishes[club] = club_finishes.get(club, 0) + 1

    finishes_total = len(rows)
    age_groups = [
        {
            "group": group,
            "finishes": count,
            "share_pct": round(count / finishes_total * 100, 1),
        }
        for group, count in sorted(age_counts.items(), key=lambda item: item[0])
    ]
    genders = [
        {
            "label": label,
            "finishes": count,
            "share_pct": round(count / sum(gender_counts.values()) * 100, 1),
        }
        for label, count in sorted(gender_counts.items(), key=lambda item: -item[1])
    ]
    clubs = sorted(
        (
            {"club": club, "people": len(members), "finishes": club_finishes[club]}
            for club, members in club_people.items()
        ),
        key=lambda item: (-int(item["finishes"]), str(item["club"])),
    )[:15]

    base.update(
        {
            "finishes_total": finishes_total,
            "people_total": len(people),
            "age_groups": age_groups,
            "genders": genders,
            "clubs": clubs,
        }
    )
    return base


# ===== Сравнение с соседями =====


def benchmark_cache_key(identity_key: str, months: int, scope: str) -> str:
    return f"organizer:benchmark:v2:{identity_key}:{months}:{scope}"


def network_metrics_cache_key(platform_code: str, months: int) -> str:
    # v2 — в метриках появились координаты (скоуп «3 ближайшие локации»).
    return f"organizer:network-metrics:v2:{platform_code}:{months}"


def _network_location_metrics(
    db: Session, platform_code: str, *, months: int
) -> list[dict[str, Any]]:
    """Метрики всех локаций системы за период — основа для бенчмарка.

    Один проход агрегатов на всю систему, кэш на сутки: считать это на каждый
    заход организатора нельзя, а меняется оно раз в неделю.
    """
    cache_key = network_metrics_cache_key(platform_code, months)
    cached = _read_json_cache(cache_key)
    if cached is not None:
        return list(cached.get("items", []))

    since = _period_start(months)
    secondary_events = select(EventCrosslink.secondary_event_id)
    event_filter = (
        Platform.code == platform_code,
        Event.event_date >= since,
        Event.is_test_event.is_(False),
        Event.id.notin_(secondary_events),
    )

    events_rows = (
        db.query(
            Location.id,
            Location.name,
            Location.city,
            Location.region,
            Location.latitude,
            Location.longitude,
            func.count(func.distinct(Event.id)),
        )
        .join(Event, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(*event_filter)
        .group_by(Location.id, Location.name, Location.city, Location.region)
        .all()
    )
    runs_rows = (
        db.query(
            Event.location_id,
            func.count(RunResult.id),
            func.count(func.distinct(RunResult.participant_id)),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(*event_filter)
        .group_by(Event.location_id)
        .all()
    )
    vols_rows = (
        db.query(
            Event.location_id,
            func.count(VolunteerResult.id),
            func.count(func.distinct(VolunteerResult.participant_id)),
        )
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(*event_filter)
        .group_by(Event.location_id)
        .all()
    )
    female_rows = (
        db.query(Event.location_id, func.count(RunResult.id))
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .filter(*event_filter, Participant.gender == "female")
        .group_by(Event.location_id)
        .all()
    )
    runs = {row[0]: (int(row[1]), int(row[2])) for row in runs_rows}
    vols = {row[0]: (int(row[1]), int(row[2])) for row in vols_rows}
    female = {row[0]: int(row[1]) for row in female_rows}

    items: list[dict[str, Any]] = []
    for location_id, name, city, region, latitude, longitude, events_count in events_rows:
        events_count = int(events_count)
        if events_count < 5:
            # Молодые и почти не стартовавшие локации искажают сравнение.
            continue
        finishes, unique_runners = runs.get(location_id, (0, 0))
        vol_slots, unique_vols = vols.get(location_id, (0, 0))
        if finishes == 0:
            continue
        items.append(
            {
                "location_id": str(location_id),
                "name": name,
                "city": city,
                "region": region,
                "latitude": latitude,
                "longitude": longitude,
                "events": events_count,
                "avg_finishers": round(finishes / events_count, 1),
                "avg_volunteers": round(vol_slots / events_count, 1),
                "unique_runners": unique_runners,
                "unique_volunteers": unique_vols,
                "female_share_pct": round(female.get(location_id, 0) / finishes * 100, 1),
                "volunteer_rotation_pct": (
                    round(unique_vols / vol_slots * 100) if vol_slots else 0
                ),
            }
        )
    _write_json_cache(cache_key, {"items": items}, NETWORK_CACHE_TTL_SECONDS)
    return items


def build_benchmark(
    db: Session,
    identity: LocationIdentity,
    *,
    months: int = DEFAULT_MONTHS,
    scope: str = "city",
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = benchmark_cache_key(identity.identity_key, months, scope)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached
    payload = _compute_benchmark(db, identity, months=months, scope=scope)
    if use_cache:
        _write_json_cache(cache_key, payload, ANALYTICS_CACHE_TTL_SECONDS)
    return payload


# Метрики, по которым считаем место локации. asc=True — «меньше значит лучше».
BENCHMARK_METRICS: tuple[tuple[str, str], ...] = (
    ("avg_finishers", "Финишёров на старте"),
    ("avg_volunteers", "Волонтёров на старте"),
    ("unique_runners", "Разных участников за период"),
    ("unique_volunteers", "Разных волонтёров за период"),
    ("female_share_pct", "Доля женщин, %"),
    ("volunteer_rotation_pct", "Ротация волонтёров, %"),
)


def _nearest_peers(
    items: list[dict[str, Any]], ours: dict[str, Any], *, count: int
) -> list[dict[str, Any]]:
    """Наша локация + count ближайших по прямой (хаверсин по координатам)."""
    from math import asin, cos, radians, sin, sqrt

    our_lat, our_lon = ours.get("latitude"), ours.get("longitude")
    if our_lat is None or our_lon is None:
        return []

    def distance_km(item: dict[str, Any]) -> float | None:
        lat, lon = item.get("latitude"), item.get("longitude")
        if lat is None or lon is None:
            return None
        lat1, lon1, lat2, lon2 = map(radians, (our_lat, our_lon, lat, lon))
        h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
        return 2 * 6371 * asin(sqrt(h))

    ranked = sorted(
        (
            (dist, item)
            for item in items
            if item["location_id"] != ours["location_id"]
            and (dist := distance_km(item)) is not None
        ),
        key=lambda pair: pair[0],
    )
    return [ours] + [item for _dist, item in ranked[:count]]


def _compute_benchmark(
    db: Session, identity: LocationIdentity, *, months: int, scope: str
) -> dict[str, Any]:
    """Наша локация против соседей: город, регион или вся система."""
    platform_code = next((code for _loc, code in identity.locations), None)
    our_ids = {str(location.id) for location, _code in identity.locations}
    base: dict[str, Any] = {
        "location": {"slug": identity.slug, "name": identity.name},
        "months": months,
        "scope": scope,
        "scope_label": "",
        "peers_total": 0,
        "scope_sizes": {},
        "metrics": [],
        "peers": [],
    }
    if platform_code is None:
        return base

    items = _network_location_metrics(db, platform_code, months=months)
    ours = next((item for item in items if item["location_id"] in our_ids), None)
    if ours is None:
        return base

    city_peers = (
        [item for item in items if item.get("city") == ours["city"]] if ours.get("city") else []
    )
    region_peers = (
        [item for item in items if item.get("region") == ours["region"]]
        if ours.get("region")
        else []
    )
    nearest_peers = _nearest_peers(items, ours, count=3)

    # Честные выборки без авторасширения (правка Дмитрия 24.08.2026): если в
    # городе локация одна, фронт просто не покажет такую вкладку — размеры
    # каждого скоупа отдаём в scope_sizes.
    base["scope_sizes"] = {
        "city": len(city_peers),
        "region": len(region_peers),
        "nearest": len(nearest_peers),
        "network": len(items),
    }

    if scope == "city" and len(city_peers) >= 2:
        peers = city_peers
        scope_label = f"город {ours['city']}"
    elif scope == "region" and len(region_peers) >= 2:
        peers = region_peers
        scope_label = f"регион {ours['region']}"
    elif scope == "nearest" and len(nearest_peers) >= 2:
        peers = nearest_peers
        scope_label = "3 ближайшие локации"
    else:
        peers = items
        scope_label = "вся система"

    metrics: list[dict[str, Any]] = []
    for key, label in BENCHMARK_METRICS:
        values = sorted((float(item[key]) for item in peers), reverse=True)
        our_value = float(ours[key])
        rank = values.index(our_value) + 1 if our_value in values else None
        median = values[len(values) // 2] if values else None
        metrics.append(
            {
                "key": key,
                "label": label,
                "our_value": our_value,
                "median": median,
                "best": values[0] if values else None,
                "rank": rank,
                "peers": len(values),
                "delta_vs_median_pct": (
                    round((our_value - median) / median * 100) if median else None
                ),
            }
        )

    top_peers = sorted(peers, key=lambda item: -float(item["avg_finishers"]))[:12]
    base.update(
        {
            "scope_label": scope_label,
            "peers_total": len(peers),
            "metrics": metrics,
            "peers": [
                {**item, "is_ours": item["location_id"] in our_ids} for item in top_peers
            ],
        }
    )
    return base
