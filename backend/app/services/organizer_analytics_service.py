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

from sqlalchemy import case, func, select
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
from app.services.location_freshness import write_organizer_cache
from app.services.location_page_service import (
    LocationIdentity,
    _location_event_ids,
    _platform_link_join,
    _read_json_cache,
    _write_json_cache,
)
from app.services.newcomer_counts import debutants_sum, first_at_location_sum
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


# Возрастная категория в протоколах склеена с полом: «М35-39», «Ж10-14», у
# parkrun-эпохи ещё и со ступенью — «VM40-44», «JW11-14», «SM25-29». Для
# пирамиды нужен чистый диапазон лет, а пол берём из participants.gender —
# он материализован и надёжнее разбора буквы.
_AGE_PREFIX_RE = re.compile(r"^[A-Za-zА-Яа-яЁё]+")
# Буквы пола в хвосте префикса: русское М/Ж и parkrun-овские M/W.
_PREFIX_GENDER = {"М": "male", "M": "male", "Ж": "female", "W": "female"}
# Всё, что начинается со 100 лет и старше, сливаем в одну строку: там и
# честная parkrun-категория «100+», и мусор данных вроде «М120».
_AGE_CENTENARIAN = "100+"


def _age_range(value: str | None) -> str | None:
    """«М35-39» → «35-39», «VM100+» → «100+», «М120» → «100+»."""
    cleaned = _clean_age_group(value)
    if not cleaned:
        return None
    stripped = _AGE_PREFIX_RE.sub("", cleaned).strip()
    if not stripped:
        return None
    start = _age_range_start(stripped)
    if start is not None and start >= 100:
        return _AGE_CENTENARIAN
    return stripped


def _age_range_start(value: str) -> int | None:
    digits = ""
    for char in value:
        if char.isdigit():
            digits += char
        else:
            break
    return int(digits) if digits else None


def _gender_from_category(value: str | None) -> str | None:
    """Запасной путь, когда пол участника не проставлен: буква из категории."""
    cleaned = _clean_age_group(value)
    if not cleaned:
        return None
    prefix = _AGE_PREFIX_RE.match(cleaned)
    if prefix is None:
        return None
    return _PREFIX_GENDER.get(prefix.group(0)[-1].upper())


# ===== Нагрузка на команду и bus-фактор =====


def team_load_cache_key(identity_key: str, months: int) -> str:
    # v3 — появился поимённый список организаторов.
    return f"organizer:team:v3:{identity_key}:{months}"


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
        write_organizer_cache(identity, cache_key, payload, ANALYTICS_CACHE_TTL_SECONDS)
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
        "organizers": [],
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

    # Поимённо, кто и сколько раз вёл старт: светофор ротации называет только
    # самого частого, а организатору нужен весь список (просьба Дмитрия
    # 17.09.2026) — по нему видно, кого пора звать обратно.
    organizer_people = role_people.get(ORGANIZER_ROLE_KEY) or {}
    organizer_slots = sum(organizer_people.values())
    organizers = sorted(
        (
            {
                "participant_id": str(pid),
                "name": person_names.get(pid, (None, None))[0],
                "slots": count,
                "share_pct": round(count / organizer_slots * 100) if organizer_slots else 0,
                "runs_here": runs_here.get(pid, 0),
            }
            for pid, count in organizer_people.items()
        ),
        key=lambda item: (-int(item["slots"]), str(item["name"] or "")),
    )

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
            "organizers": organizers,
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
        write_organizer_cache(identity, cache_key, payload, ANALYTICS_CACHE_TTL_SECONDS)
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
    # v2 — возрастные группы поехали пирамидой «мужчины слева, женщины справа».
    return f"organizer:audience:v2:{identity_key}:{months}"


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
        write_organizer_cache(identity, cache_key, payload, ANALYTICS_CACHE_TTL_SECONDS)
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
        "age_pyramid": [],
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

    # Возрастная пирамида: строка = диапазон лет, в ней два числа — мужчины и
    # женщины. Раньше это был один общий список категорий, а они склеены с
    # полом («Ж35-39», «М35-39») и по алфавиту вставали двумя блоками друг под
    # другом — женский график над мужским. Читать это оказалось неудобно
    # (заявка из бэклога сайта), поэтому пол ушёл на две стороны одной строки.
    age_cells: dict[str, dict[str, int]] = {}
    gender_counts: dict[str, int] = {}
    club_people: dict[str, set[Any]] = {}
    club_finishes: dict[str, int] = {}
    people: set[Any] = set()
    for pid, age_category, gender, club_name in rows:
        people.add(pid)
        resolved_gender = (gender or "").lower()
        if resolved_gender not in ("male", "female"):
            resolved_gender = _gender_from_category(age_category) or ""
        age_range = _age_range(age_category)
        if age_range and resolved_gender in ("male", "female"):
            cell = age_cells.setdefault(age_range, {"male": 0, "female": 0})
            cell[resolved_gender] += 1
        key = {"male": "Мужчины", "female": "Женщины"}.get(resolved_gender)
        if key:
            gender_counts[key] = gender_counts.get(key, 0) + 1
        club = (club_name or "").strip()
        if club:
            club_people.setdefault(club, set()).add(pid)
            club_finishes[club] = club_finishes.get(club, 0) + 1

    finishes_total = len(rows)
    age_pyramid = [
        {
            "range": age_range,
            "male_finishes": cell["male"],
            "female_finishes": cell["female"],
            "male_share_pct": round(cell["male"] / finishes_total * 100, 1),
            "female_share_pct": round(cell["female"] / finishes_total * 100, 1),
        }
        for age_range, cell in sorted(
            age_cells.items(),
            # Сортировка по началу диапазона, а не по строке: иначе «10-14»
            # оказывается между «100+» и «15-19».
            key=lambda item: (_age_range_start(item[0]) or 0, item[0]),
        )
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
            "age_pyramid": age_pyramid,
            "genders": genders,
            "clubs": clubs,
        }
    )
    return base


# ===== Сравнение с соседями =====


def benchmark_cache_key(identity_key: str, months: int, scope: str, peer_key: str = "") -> str:
    # v3 — появился скоуп «одна локация»: в ключ уехал её identity.
    return f"organizer:benchmark:v3:{identity_key}:{months}:{scope}:{peer_key}"


def network_metrics_cache_key(platform_code: str, months: int) -> str:
    # v3 — метрик стало вдвое больше: время, новички, гости, личные рекорды.
    return f"organizer:network-metrics:v3:{platform_code}:{months}"


def _network_guests_by_location(
    db: Session, platform_code: str, *, since: date
) -> dict[Any, int]:
    """Гостей на каждой площадке системы за период: location_id → число.

    Гость — финишёр, чей дом другая площадка ([[location_guests_service]]).
    Для одной локации дом считает `participant_home_keys` (общесайтовая логика
    с ручным выбором и волонтёрствами), но на всю систему такой проход по
    участникам не годится — здесь один SQL: дом = площадка с наибольшим числом
    финишей, при равенстве — где человек начал раньше. Участник заведён внутри
    системы, поэтому его история целиком в этой же системе и ответы расходятся
    лишь там, где одна физическая площадка заведена в системе двумя строками.

    Разница с числом на странице локации ещё и в окне: там вся история, здесь
    выбранный период. Обе цифры честные, но сравнивать их между собой нельзя.
    """
    hist = (
        select(
            RunResult.participant_id.label("participant_id"),
            Event.location_id.label("location_id"),
            func.count().label("runs"),
            func.min(Event.event_date).label("first_date"),
        )
        .join(Event, Event.id == RunResult.event_id)
        .join(Location, Location.id == Event.location_id)
        .join(Platform, Platform.id == Location.platform_id)
        .where(
            Platform.code == platform_code,
            RunResult.finish_time_sec.isnot(None),
            Event.is_test_event.is_(False),
            Event.id.notin_(select(EventCrosslink.secondary_event_id)),
        )
        .group_by(RunResult.participant_id, Event.location_id)
        .cte("guest_history")
    )
    home = (
        select(hist.c.participant_id, hist.c.location_id)
        .distinct(hist.c.participant_id)
        .order_by(
            hist.c.participant_id,
            hist.c.runs.desc(),
            hist.c.first_date.asc(),
            hist.c.location_id.asc(),
        )
        .cte("guest_home")
    )
    rows = (
        db.query(
            Event.location_id,
            func.count().filter(home.c.location_id != Event.location_id),
        )
        # Явный select_from: первая колонка выборки — из Event, и без него
        # SQLAlchemy не понимает, к чему присоединять сам Event.
        .select_from(RunResult)
        .join(Event, Event.id == RunResult.event_id)
        .join(Location, Location.id == Event.location_id)
        .join(Platform, Platform.id == Location.platform_id)
        .join(home, home.c.participant_id == RunResult.participant_id)
        .filter(
            Platform.code == platform_code,
            Event.event_date >= since,
            Event.is_test_event.is_(False),
            Event.id.notin_(select(EventCrosslink.secondary_event_id)),
            RunResult.finish_time_sec.isnot(None),
        )
        .group_by(Event.location_id)
        .all()
    )
    return {location_id: int(count or 0) for location_id, count in rows}


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
    time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
    runs_rows = (
        db.query(
            Event.location_id,
            func.count(RunResult.id),
            func.count(func.distinct(RunResult.participant_id)),
            func.avg(case((time_ok, RunResult.finish_time_sec))),
            debutants_sum(),
            first_at_location_sum(),
            func.sum(case((RunResult.is_pr.is_(True), 1), else_=0)),
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
    # Организаторы отдельной выборкой: канонизировать роль в SQL нельзя, а
    # знать, на скольких людях держится старт, организатор хочет не только у
    # себя (просьба Дмитрия 17.09.2026).
    organizer_rows = (
        db.query(Event.location_id, VolunteerResult.role, VolunteerResult.participant_id)
        .select_from(VolunteerResult)
        .join(Event, Event.id == VolunteerResult.event_id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(
            *event_filter,
            VolunteerResult.role.isnot(None),
            VolunteerResult.participant_id.isnot(None),
        )
        .all()
    )
    organizer_slots: dict[Any, int] = {}
    organizer_people: dict[Any, set[Any]] = {}
    for location_id, role, participant_id in organizer_rows:
        canonical = canonical_volunteer_role(role)
        if canonical is None or canonical.key != ORGANIZER_ROLE_KEY:
            continue
        organizer_slots[location_id] = organizer_slots.get(location_id, 0) + 1
        organizer_people.setdefault(location_id, set()).add(participant_id)

    # Медианная задержка выгрузки протокола — уже считается для светофора
    # скорости протоколов, наблюдение есть только у 5 вёрст.
    from app.services.organizer_protocol_service import network_protocol_medians

    protocol_delays = network_protocol_medians(db) if platform_code == "five_verst" else {}

    runs = {
        row[0]: {
            "finishes": int(row[1]),
            "unique_runners": int(row[2]),
            "avg_time": int(row[3]) if row[3] is not None else None,
            "debutants": int(row[4] or 0),
            "first_here": int(row[5] or 0),
            "prs": int(row[6] or 0),
        }
        for row in runs_rows
    }
    vols = {row[0]: (int(row[1]), int(row[2])) for row in vols_rows}
    female = {row[0]: int(row[1]) for row in female_rows}
    guests = _network_guests_by_location(db, platform_code, since=since)

    items: list[dict[str, Any]] = []
    for location_id, name, city, region, latitude, longitude, events_count in events_rows:
        events_count = int(events_count)
        if events_count < 5:
            # Молодые и почти не стартовавшие локации искажают сравнение.
            continue
        stats = runs.get(location_id)
        vol_slots, unique_vols = vols.get(location_id, (0, 0))
        if stats is None or stats["finishes"] == 0:
            continue
        finishes = stats["finishes"]
        location_guests = guests.get(location_id, 0)
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
                "unique_runners": stats["unique_runners"],
                "unique_volunteers": unique_vols,
                "avg_finish_time_sec": stats["avg_time"],
                "avg_debutants": round(stats["debutants"] / events_count, 1),
                "avg_first_here": round(stats["first_here"] / events_count, 1),
                "avg_guests": round(location_guests / events_count, 1),
                "guests_share_pct": round(location_guests / finishes * 100, 1),
                "avg_prs": round(stats["prs"] / events_count, 1),
                "female_share_pct": round(female.get(location_id, 0) / finishes * 100, 1),
                "volunteer_rotation_pct": (
                    round(unique_vols / vol_slots * 100) if vol_slots else 0
                ),
                "organizers_count": len(organizer_people.get(location_id) or ()),
                "organizer_rotation_pct": (
                    round(len(organizer_people[location_id]) / organizer_slots[location_id] * 100)
                    if organizer_slots.get(location_id)
                    else 0
                ),
                # None — за площадкой не наблюдаем (не 5 вёрст) либо фактов
                # меньше пяти: строка сравнения тогда просто не показывается.
                "protocol_delay_hours": protocol_delays.get(str(location_id)),
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
    peer: LocationIdentity | None = None,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = benchmark_cache_key(
        identity.identity_key, months, scope, peer.identity_key if peer else ""
    )
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached
    payload = _compute_benchmark(db, identity, months=months, scope=scope, peer=peer)
    if use_cache:
        write_organizer_cache(identity, cache_key, payload, ANALYTICS_CACHE_TTL_SECONDS)
    return payload


# Метрики бенчмарка: ключ, подпись, группа и «больше значит лучше».
#
# higher_is_better = None оставлено для метрик-профилей, где «лучше» не бывает
# ни в какую сторону: место в выборке им не считается и процент не красится.
# Сейчас такая одна — доля женщин.
BENCHMARK_METRICS: tuple[tuple[str, str, str, bool | None], ...] = (
    ("avg_finishers", "Финишёров на старте", "Явка", True),
    ("unique_runners", "Разных участников за период", "Явка", True),
    ("events", "Стартов за период", "Явка", True),
    ("female_share_pct", "Доля женщин, %", "Явка", None),
    # Время финиша здесь — про поле, а не про спортсмена: чем оно больше, тем
    # больше на старте неспешных бегунов, семей и новичков, а это ровно то, за
    # чем локация и существует (решение Дмитрия 14.09.2026).
    ("avg_finish_time_sec", "Среднее время финиша", "Результаты", True),
    ("avg_prs", "Личных рекордов на старте", "Результаты", True),
    ("avg_debutants", "Новичков на старте", "Новые лица", True),
    ("avg_first_here", "Впервые здесь, на старте", "Новые лица", True),
    ("avg_guests", "Гостей на старте", "Новые лица", True),
    ("guests_share_pct", "Доля гостей, %", "Новые лица", True),
    ("avg_volunteers", "Волонтёров на старте", "Команда", True),
    ("unique_volunteers", "Разных волонтёров за период", "Команда", True),
    ("volunteer_rotation_pct", "Ротация волонтёров, %", "Команда", True),
    ("organizers_count", "Разных организаторов за период", "Команда", True),
    ("organizer_rotation_pct", "Ротация организаторов, %", "Команда", True),
    # Наблюдение за выгрузкой есть только у 5 вёрст: у остальных систем строка
    # не появится вовсе (метрика None → её пропускает _compute_benchmark).
    ("protocol_delay_hours", "Протокол после финиша, часов", "Команда", False),
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


def _location_metrics_for(
    db: Session, identity: LocationIdentity, *, months: int
) -> dict[str, Any] | None:
    """Строка метрик конкретной локации из среза её системы (или None)."""
    platform_code = next((code for _loc, code in identity.locations), None)
    if platform_code is None:
        return None
    ids = {str(location.id) for location, _code in identity.locations}
    items = _network_location_metrics(db, platform_code, months=months)
    return next((item for item in items if item["location_id"] in ids), None)


def _compute_benchmark(
    db: Session,
    identity: LocationIdentity,
    *,
    months: int,
    scope: str,
    peer: LocationIdentity | None = None,
) -> dict[str, Any]:
    """Наша локация против соседей: город, регион, вся система или одна площадка."""
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
        "peer_location": None,
        "peer_note": None,
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

    # Сравнение с одной выбранной локацией — отдельная ветка: медиана и место в
    # выборке из двух строк ничего не значат, показываем «мы против них».
    # Локация может быть из другой системы, поэтому её метрики берём из среза
    # ЕЁ системы (заявка из бэклога сайта).
    if scope == "location":
        if peer is None:
            base["peer_note"] = "Выберите локацию для сравнения"
            return base
        base["peer_location"] = {"slug": peer.slug, "name": peer.name}
        base["scope_label"] = peer.name
        theirs = _location_metrics_for(db, peer, months=months)
        if theirs is None:
            base["peer_note"] = "За выбранный период у этой локации меньше пяти стартов"
            return base
        base["metrics"] = [
            {
                "key": key,
                "label": label,
                "group": group,
                "higher_is_better": higher_is_better,
                "our_value": float(ours[key]),
                "peer_value": float(theirs[key]),
                "peers": 1,
                "delta_vs_peer_pct": (
                    round((float(ours[key]) - float(theirs[key])) / float(theirs[key]) * 100)
                    if float(theirs[key])
                    else None
                ),
            }
            for key, label, group, higher_is_better in BENCHMARK_METRICS
            if ours.get(key) is not None and theirs.get(key) is not None
        ]
        base["peers_total"] = 1
        base["peers"] = [
            {**ours, "is_ours": True},
            {**theirs, "is_ours": False},
        ]
        return base

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
    for key, label, group, higher_is_better in BENCHMARK_METRICS:
        if ours.get(key) is None:
            # У площадки нет такой цифры (например, ни одного времени финиша) —
            # строку не показываем вовсе, чтобы не сравнивать с прочерком.
            continue
        # Сортируем так, чтобы values[0] всегда была «лучшая»: у времени финиша
        # это минимум, у остальных — максимум.
        values = sorted(
            (float(item[key]) for item in peers if item.get(key) is not None),
            reverse=higher_is_better is not False,
        )
        our_value = float(ours[key])
        rank = values.index(our_value) + 1 if higher_is_better is not None and our_value in values else None
        median = sorted(values)[len(values) // 2] if values else None
        metrics.append(
            {
                "key": key,
                "label": label,
                "group": group,
                "higher_is_better": higher_is_better,
                "our_value": our_value,
                "median": median,
                # «Лучшая» бессмысленна там, где лучше не бывает (доля женщин).
                "best": values[0] if values and higher_is_better is not None else None,
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
