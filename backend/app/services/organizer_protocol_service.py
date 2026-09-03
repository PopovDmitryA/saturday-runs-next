"""Кабинет организатора: скорость выгрузки протоколов и светофор здоровья.

Данные о моменте появления протокола — protocol_upload_facts (поминутный
наблюдатель /results/latest/ + импорт легаси-истории с 08.2024). Задержка
считается по формуле Дмитрия (23.08.2026): от момента, когда финишировал
ПОСЛЕДНИЙ участник, а не от выстрела стартового пистолета:

    задержка = first_seen_at − (дата + местное время старта − пояс + финиш последнего)

Время старта — schedule_parsed описания локации (сезонные окна), пояс —
locations.tz_offset_moscow (Москва = UTC+3). Фаза 1 — только 5 вёрст:
у S95 нет момента публикации в API (updated_at — ночной пересчёт).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    LocationDescription,
    Participant,
    Platform,
    ProtocolRevision,
    ProtocolUploadFact,
    RunResult,
    VolunteerResult,
)
from app.services.location_page_service import LocationIdentity, _location_event_ids
from app.services.location_schedule_service import start_time_for_date
from app.services.organizer_analytics_service import _read_json_cache, _write_json_cache
from app.volunteer_role_taxonomy import canonical_volunteer_role

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3 * 60 * 60
NETWORK_CACHE_TTL_SECONDS = 24 * 60 * 60

# Светофор протокола: 🟢 в первые 3 часа от финиша последнего, 🟡 день в день
# (по местному дню локации), 🔴 на следующий день и позже.
GREEN_DELAY_HOURS = 3.0


def protocol_timeline_cache_key(identity_key: str) -> str:
    # v3 — старты до начала наблюдения за выгрузкой в список не попадают.
    return f"organizer:protocols:v3:{identity_key}"


def health_cache_key(identity_key: str) -> str:
    # v7 — добавилась лампочка «Ротация организаторов». Без бампа площадки с
    # прогретым кэшем показывали бы светофор без неё до истечения TTL.
    return f"organizer:health:v7:{identity_key}"


def _five_verst_locations(identity: LocationIdentity) -> list[Location]:
    return [location for location, code in identity.locations if code == "five_verst"]


def _identity_tz_offset(locations: list[Location]) -> int:
    for location in locations:
        if location.tz_offset_moscow is not None:
            return int(location.tz_offset_moscow)
    return 0


def _identity_schedule(db: Session, locations: list[Location]) -> list[dict[str, Any]]:
    rows = (
        db.query(LocationDescription)
        .filter(LocationDescription.location_id.in_([location.id for location in locations]))
        .all()
    )
    for row in rows:
        if row.schedule_parsed:
            return row.schedule_parsed
    return []


def _utc_finish_moment(
    event_date: date,
    start_time_local: time,
    tz_offset_moscow: int,
    last_finish_sec: int | None,
) -> datetime:
    """Момент финиша последнего участника в UTC (Москва = UTC+3)."""

    start_local = datetime.combine(event_date, start_time_local)
    start_utc = start_local - timedelta(hours=3 + tz_offset_moscow)
    return start_utc.replace(tzinfo=timezone.utc) + timedelta(seconds=last_finish_sec or 0)


def _delay_level(delay_hours: float | None, published_same_local_day: bool | None) -> str | None:
    if delay_hours is None:
        return None
    if delay_hours <= GREEN_DELAY_HOURS:
        return "green"
    if published_same_local_day:
        return "yellow"
    return "red"


def build_protocol_timeline(db: Session, identity: LocationIdentity, *, limit: int = 300) -> dict[str, Any]:
    cached = _read_json_cache(protocol_timeline_cache_key(identity.identity_key))
    if cached is not None:
        return cached
    payload = _compute_protocol_timeline(db, identity, limit=limit)
    _write_json_cache(protocol_timeline_cache_key(identity.identity_key), payload, CACHE_TTL_SECONDS)
    return payload


def _compute_protocol_timeline(db: Session, identity: LocationIdentity, *, limit: int) -> dict[str, Any]:
    fv_locations = _five_verst_locations(identity)
    base = {
        "location": {"slug": identity.slug, "name": identity.name},
        "supported": bool(fv_locations),
        "tz_offset_moscow": _identity_tz_offset(fv_locations) if fv_locations else 0,
        "items": [],
        "median_delay_hours_12m": None,
        "network_rank": None,
        "network_size": None,
    }
    if not fv_locations:
        return base

    fv_ids = [location.id for location in fv_locations]
    tz_offset = base["tz_offset_moscow"]
    schedule = _identity_schedule(db, fv_locations)

    events = (
        db.query(Event)
        .filter(Event.location_id.in_(fv_ids))
        .order_by(Event.event_date.desc())
        .limit(limit)
        .all()
    )
    event_ids = [event.id for event in events]

    finish_rows = (
        db.query(
            RunResult.event_id,
            func.count().filter(RunResult.finish_time_sec.isnot(None)),
            func.max(RunResult.finish_time_sec),
        )
        .filter(RunResult.event_id.in_(event_ids))
        .group_by(RunResult.event_id)
        .all()
    )
    finish_by_event = {event_id: (int(count or 0), max_sec) for event_id, count, max_sec in finish_rows}

    # Только подтверждённые наблюдением моменты. «Увидели протокол уже
    # лежащим» — это не выгрузка в тот момент, а отсутствие данных: строка
    # уйдёт в прочерк, задержка не посчитается, ярлык «с опозданием» не
    # появится. Лучше пустота, чем ложное обвинение (Дмитрий 03.09.2026).
    facts = (
        db.query(ProtocolUploadFact)
        .filter(
            ProtocolUploadFact.location_id.in_(fv_ids),
            ProtocolUploadFact.first_seen_confirmed.is_(True),
        )
        .all()
    )
    fact_by_date = {fact.event_date: fact for fact in facts}

    directors: dict[Any, list[str]] = {}
    volunteer_rows = (
        db.query(VolunteerResult.event_id, VolunteerResult.role, Participant.display_name)
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .filter(VolunteerResult.event_id.in_(event_ids))
        .all()
    )
    for event_id, role, name in volunteer_rows:
        canonical = canonical_volunteer_role(role) if role else None
        if canonical is not None and canonical.key == "run_director" and name:
            directors.setdefault(event_id, []).append(name)

    revision_rows = (
        db.query(ProtocolRevision)
        .filter(ProtocolRevision.event_id.in_(event_ids))
        .order_by(ProtocolRevision.detected_at)
        .all()
    )
    revisions_by_event: dict[Any, list[dict[str, Any]]] = {}
    for revision in revision_rows:
        revisions_by_event.setdefault(revision.event_id, []).append(
            {
                "detected_at": revision.detected_at.isoformat(),
                "kind": revision.kind,
                "details": revision.details or {},
            }
        )

    items: list[dict[str, Any]] = []
    delays_12m: list[float] = []
    cutoff_12m = date.today() - timedelta(days=365)
    for event in events:
        finishers, last_finish_sec = finish_by_event.get(event.id, (0, None))
        start_local = start_time_for_date(schedule, event.event_date)
        fact = fact_by_date.get(event.event_date)

        delay_hours: float | None = None
        published_same_day: bool | None = None
        first_seen_iso: str | None = None
        first_seen_local_display: str | None = None
        if fact is not None:
            first_seen_iso = fact.first_seen_at.isoformat()
            # astimezone, а не «+3 часа»: соединение с БД отдаёт момент уже в
            # московском поясе, и наивное сложение уводило показ на три часа
            # вперёд — 21:00 превращалось в «00:00 следующего дня».
            local_seen = fact.first_seen_at.astimezone(timezone(timedelta(hours=3 + tz_offset)))
            first_seen_local_display = local_seen.strftime("%d.%m %H:%M")
            published_same_day = local_seen.date() == event.event_date
            if start_local is not None:
                finish_utc = _utc_finish_moment(event.event_date, start_local, tz_offset, last_finish_sec)
                delay_hours = round((fact.first_seen_at - finish_utc).total_seconds() / 3600.0, 1)

        level = _delay_level(delay_hours, published_same_day)
        if delay_hours is not None and event.event_date >= cutoff_12m:
            delays_12m.append(delay_hours)

        last_finish_display = None
        if last_finish_sec:
            last_finish_display = f"{last_finish_sec // 60}:{last_finish_sec % 60:02d}"

        items.append(
            {
                "date": event.event_date.isoformat(),
                "date_display": event.event_date.strftime("%d.%m.%Y"),
                "event_number": event.event_number,
                "start_time": start_local.strftime("%H:%M") if start_local else None,
                "finishers": finishers,
                "last_finish_display": last_finish_display,
                "first_seen_at": first_seen_iso,
                "first_seen_display": first_seen_local_display,
                "delay_hours": delay_hours,
                "level": level,
                "directors": sorted(set(directors.get(event.id, []))),
                "revisions": revisions_by_event.get(event.id, []),
            }
        )

    # Старты до начала наблюдения показывать не стоит: у них не может быть
    # момента публикации, и таблица уходила в 2022 год пустыми строками.
    if fact_by_date:
        watch_started = min(fact_by_date)
        items = [item for item in items if item["date"] >= watch_started.isoformat()]

    base["items"] = items
    if delays_12m:
        base["median_delay_hours_12m"] = round(median(delays_12m), 1)
        network = _network_protocol_medians(db)
        if network:
            values = sorted(network.values())
            our = base["median_delay_hours_12m"]
            base["network_size"] = len(values)
            base["network_rank"] = 1 + sum(1 for value in values if value < our)
    return base


def _network_protocol_medians(db: Session) -> dict[str, float]:
    """Медианная задержка выгрузки за 12 мес по каждой 5в-локации (кэш сутки).

    Считается в Python: сезонные окна расписания из jsonb в SQL раскрывать
    дороже, чем перебрать ~10 тыс. фактов за год.
    """

    cache_key = "organizer:network-protocol:v1"
    cached = _read_json_cache(cache_key)
    if cached is not None:
        return {key: float(value) for key, value in cached.items()}

    cutoff = date.today() - timedelta(days=365)
    platform = db.query(Platform).filter(Platform.code == "five_verst").one()

    locations = db.query(Location).filter(Location.platform_id == platform.id).all()
    tz_by_location = {location.id: int(location.tz_offset_moscow or 0) for location in locations}
    schedule_by_location: dict[Any, list[dict[str, Any]]] = {}
    for row in (
        db.query(LocationDescription)
        .filter(LocationDescription.location_id.in_(list(tz_by_location)))
        .all()
    ):
        if row.schedule_parsed:
            schedule_by_location[row.location_id] = row.schedule_parsed

    finish_rows = (
        db.query(Event.location_id, Event.event_date, func.max(RunResult.finish_time_sec))
        .join(RunResult, RunResult.event_id == Event.id)
        .filter(Event.platform_id == platform.id, Event.event_date >= cutoff)
        .group_by(Event.location_id, Event.event_date)
        .all()
    )
    finish_by_key = {(location_id, event_date): max_sec for location_id, event_date, max_sec in finish_rows}

    facts = (
        db.query(ProtocolUploadFact)
        .filter(ProtocolUploadFact.event_date >= cutoff)
        .all()
    )

    delays_by_location: dict[Any, list[float]] = {}
    for fact in facts:
        schedule = schedule_by_location.get(fact.location_id)
        if not schedule:
            continue
        start_local = start_time_for_date(schedule, fact.event_date)
        if start_local is None:
            continue
        finish_utc = _utc_finish_moment(
            fact.event_date,
            start_local,
            tz_by_location.get(fact.location_id, 0),
            finish_by_key.get((fact.location_id, fact.event_date)),
        )
        delay_hours = (fact.first_seen_at - finish_utc).total_seconds() / 3600.0
        delays_by_location.setdefault(fact.location_id, []).append(delay_hours)

    payload = {
        str(location_id): round(median(values), 2)
        for location_id, values in delays_by_location.items()
        if len(values) >= 5
    }
    _write_json_cache(cache_key, payload, NETWORK_CACHE_TTL_SECONDS)
    return {key: float(value) for key, value in payload.items()}


# ===== Светофор здоровья локации =====


def build_location_health(db: Session, identity: LocationIdentity) -> dict[str, Any]:
    cached = _read_json_cache(health_cache_key(identity.identity_key))
    if cached is not None:
        return cached
    payload = _compute_location_health(db, identity)
    _write_json_cache(health_cache_key(identity.identity_key), payload, CACHE_TTL_SECONDS)
    return payload


def _health_level_vs_median(our_value: float | None, network_median: float | None) -> str | None:
    """🟢 выше медианы системы, 🟡 в пределах −15% от неё, 🔴 ниже."""

    if our_value is None or network_median is None:
        return None
    if our_value >= network_median:
        return "green"
    if network_median > 0 and our_value >= network_median * 0.85:
        return "yellow"
    return "red"


def _compute_location_health(db: Session, identity: LocationIdentity) -> dict[str, Any]:
    indicators: list[dict[str, Any]] = []

    # 1. Протокол: медиана задержки последних 12 стартов с фактами.
    # У локаций без 5в-половины наблюдателя нет — индикатор не показываем
    # вовсе (решение Дмитрия 24.08.2026): серая точка без данных только путает.
    timeline = build_protocol_timeline(db, identity)
    recent = [item for item in timeline["items"] if item["delay_hours"] is not None][:12]
    protocol_level = None
    protocol_value = None
    if recent:
        protocol_value = round(median(item["delay_hours"] for item in recent), 1)
        same_day = sum(1 for item in recent if item["level"] in ("green", "yellow")) >= (len(recent) + 1) // 2
        protocol_level = _delay_level(protocol_value, same_day)
    if timeline["supported"]:
        indicators.append(
            {
                "key": "protocol",
                "title": "Протокол",
                "level": protocol_level,
                "value_display": (
                    f"медиана {protocol_value} ч" if protocol_value is not None else None
                ),
                "hint": (
                    "Как быстро протокол появляется на 5 вёрст: медиана по последним "
                    "стартам, отсчёт — от финиша последнего участника. Зелёная зона — "
                    "до 3 часов, жёлтая — день в день, красная — на следующий день и позже."
                ),
                "advice": (
                    "На что влияет: участники ждут результатов в день старта — пока "
                    "протокола нет, они не увидят время, личники и юбилеи, а интерес "
                    "к субботе остывает. Медленная выгрузка бьёт по возвращаемости."
                ),
            }
        )

    # 2. Ротация волонтёров против медианы системы — из готового бенчмарка.
    from app.services.organizer_analytics_service import build_benchmark

    rotation_value = rotation_median = None
    try:
        benchmark = build_benchmark(db, identity, scope="network")
        for metric in benchmark.get("metrics", []):
            if metric.get("key") == "volunteer_rotation_pct":
                rotation_value = metric.get("our_value")
                rotation_median = metric.get("median")
                break
    except Exception:
        logger.exception("health: не удалось получить бенчмарк ротации")
    indicators.append(
        {
            "key": "rotation",
            "title": "Ротация волонтёров",
            "level": _health_level_vs_median(rotation_value, rotation_median),
            "value_display": (
                f"{rotation_value}% ({rotation_median}% в среднем по системе)"
                if rotation_value is not None and rotation_median is not None
                else None
            ),
            "hint": (
                "Доля разных людей среди волонтёрств за год: 100% — каждый раз "
                "новые люди, низкий процент — одни и те же тянут всё на себе."
            ),
            "advice": (
                "Как улучшить: зовите новых людей из «Волонтёрской скамейки» "
                "(вкладка «Кого позвать») и меняйте роли внутри команды — так "
                "никто не выгорает и старт не зависит от двух-трёх человек."
            ),
        }
    )

    # 2b. Ротация организаторов — сразу за ротацией волонтёров: роль та же по
    # смыслу, но выгорание в ней закрывает площадку целиком. Пороги свои,
    # посчитанные по стране, поэтому берём готовый уровень из «Команды и
    # нагрузки», а не сравниваем с медианой (просьба Дмитрия 03.09.2026 —
    # именно лампочкой в общем светофоре, а не отдельной карточкой).
    from app.services.organizer_analytics_service import build_team_load

    director = None
    try:
        director = build_team_load(db, identity).get("director_rotation")
    except Exception:
        logger.exception("health: не удалось получить ротацию организаторов")
    if director:
        top_share = director.get("top_share_pct")
        people = director.get("people")
        indicators.append(
            {
                "key": "director_rotation",
                "title": "Ротация организаторов",
                "level": director.get("level"),
                "value_display": (
                    f"{people} чел., на самого частого {top_share}% стартов"
                    if people is not None and top_share is not None
                    else None
                ),
                "hint": (
                    "Сколько человек вели старт за год и какую долю закрывает самый "
                    "частый. Зелёная зона — людей хотя бы четверо и на самого частого "
                    "не больше 40%; красная — организатор один или на нём больше 70%."
                ),
                "advice": (
                    "Как улучшить: готовьте сменщиков заранее — роль организатора "
                    "единственная, выгорание в которой закрывает площадку целиком."
                ),
            }
        )

    # Индикатор «фотограф на старте» убран из светофора (решение Дмитрия
    # 23.08.2026, «пока уберём») — _photographer_share остаётся на будущее.

    # 3. Тренд посещаемости: год к году, из готового кэша «Посещаемости».
    from app.services.organizer_analytics_service import build_attendance

    trend_level = None
    trend_display = None
    try:
        attendance = build_attendance(db, identity)
        yoy = attendance.get("yoy_delta_pct")
        avg = attendance.get("last_12m_avg")
        if yoy is not None:
            trend_level = "green" if yoy > 5 else "yellow" if yoy >= -5 else "red"
            sign = "+" if yoy > 0 else ""
            trend_display = f"{sign}{yoy}% к прошлому году"
            if avg is not None:
                trend_display += f" (в среднем {avg} финишёров)"
    except Exception:
        logger.exception("health: не удалось получить посещаемость")
    indicators.append(
        {
            "key": "attendance",
            "title": "Тренд посещаемости",
            "level": trend_level,
            "value_display": trend_display,
            "hint": (
                "Среднее число финишёров за последние 12 месяцев против "
                "предыдущих 12. Зелёная зона — рост больше 5%, жёлтая — "
                "стабильно (±5%), красная — падение."
            ),
            "advice": None,
        }
    )

    # 4. Полнота протокола: доля «Неизвестных» за последние 12 недель.
    unknown_share = _unknown_share(db, identity)
    quality_level = None
    if unknown_share is not None:
        quality_level = "green" if unknown_share < 2 else "yellow" if unknown_share <= 5 else "red"
    indicators.append(
        {
            "key": "protocol_quality",
            "title": "Полнота протокола",
            "level": quality_level,
            "value_display": (
                f"{unknown_share}% неизвестных финишёров" if unknown_share is not None else None
            ),
            "hint": (
                "Доля финишёров, оставшихся в протоколе «Неизвестными» за "
                "последние 12 недель: неотсканированные штрихкоды. "
                "Зелёная зона — меньше 2%, красная — больше 5%."
            ),
            "advice": None,
        }
    )

    # 5. Удержание новичков против медианы системы.
    from app.services.organizer_service import build_location_newcomers

    retention_value = None
    try:
        newcomers = build_location_newcomers(db, identity, days=180)
        retention_value = newcomers.get("retention_pct")
    except Exception:
        logger.exception("health: не удалось получить удержание новичков")
    retention_median = _network_retention_median(db)
    indicators.append(
        {
            "key": "newcomers",
            "title": "Удержание новичков",
            "level": _health_level_vs_median(retention_value, retention_median),
            "value_display": (
                f"{retention_value}% ({retention_median}% в среднем по системе)"
                if retention_value is not None and retention_median is not None
                else f"{retention_value}%" if retention_value is not None else None
            ),
            "hint": (
                "Сколько людей, чья первая пробежка в системе случилась именно у "
                "вас, вернулись хотя бы ещё раз."
            ),
            "advice": (
                "Как улучшить: встречайте дебютантов — назовите по имени на "
                "брифинге, отметьте в посте-отчёте (формат «Привет новичкам»), "
                "позовите на следующую субботу. Человек возвращается туда, где "
                "его заметили."
            ),
        }
    )

    return {
        "location": {"slug": identity.slug, "name": identity.name},
        "indicators": indicators,
    }


def _unknown_share(db: Session, identity: LocationIdentity) -> float | None:
    """Доля «Неизвестных» среди финишёров за последние 12 недель (в %)."""

    cutoff = date.today() - timedelta(days=7 * 12)
    event_ids = _location_event_ids(db, [location.id for location, _code in identity.locations])
    if not event_ids:
        return None
    recent_ids = [
        row[0]
        for row in db.query(Event.id)
        .filter(Event.id.in_(event_ids), Event.event_date >= cutoff)
        .all()
    ]
    if not recent_ids:
        return None
    total, unknown = (
        db.query(
            func.count(),
            func.count().filter(RunResult.status == "unknown"),
        )
        .filter(RunResult.event_id.in_(recent_ids))
        .one()
    )
    if not total:
        return None
    return round(100.0 * int(unknown or 0) / int(total), 1)


def _photographer_share(db: Session, identity: LocationIdentity) -> int | None:
    cutoff = date.today() - timedelta(days=7 * 12)
    event_ids = _location_event_ids(db, [location.id for location, _code in identity.locations])
    if not event_ids:
        return None
    events = (
        db.query(Event.id)
        .filter(Event.id.in_(event_ids), Event.event_date >= cutoff)
        .all()
    )
    recent_ids = [row[0] for row in events]
    if not recent_ids:
        return None
    rows = (
        db.query(VolunteerResult.event_id, VolunteerResult.role)
        .filter(VolunteerResult.event_id.in_(recent_ids))
        .all()
    )
    with_photo = set()
    for event_id, role in rows:
        canonical = canonical_volunteer_role(role) if role else None
        if canonical is not None and canonical.key == "photographer":
            with_photo.add(event_id)
    events_with_volunteers = {event_id for event_id, _role in rows}
    if not events_with_volunteers:
        return None
    return round(100 * len(with_photo) / len(events_with_volunteers))


def _network_retention_median(db: Session) -> float | None:
    """Медиана удержания новичков по локациям системы (кэш сутки).

    Упрощённая сетевая версия: дебют = первый финиш участника в системе вообще
    (по платформенным данным), вернулся = ещё один финиш на той же локации.
    Дебюты последних 180 дней, кроме самых свежих двух недель (им ещё рано
    возвращаться); локации с < 5 дебютами не учитываются.
    """

    cache_key = "organizer:network-retention:v1"
    cached = _read_json_cache(cache_key)
    if cached is not None:
        return cached.get("median")

    from sqlalchemy import text

    cutoff = date.today() - timedelta(days=180)
    recent_cap = date.today() - timedelta(days=14)
    rows = db.execute(
        text(
            """
            with firsts as (
                select rr.participant_id, min(e.event_date) as first_date
                from run_results rr
                join events e on e.id = rr.event_id
                where rr.finish_time_sec is not null
                group by rr.participant_id
                having min(e.event_date) >= :cutoff and min(e.event_date) <= :recent_cap
            ),
            debuts as (
                select f.participant_id, f.first_date,
                       (
                           select e2.location_id
                           from run_results rr2
                           join events e2 on e2.id = rr2.event_id
                           where rr2.participant_id = f.participant_id
                             and rr2.finish_time_sec is not null
                           order by e2.event_date
                           limit 1
                       ) as location_id
                from firsts f
            )
            select d.location_id,
                   count(*) as debuts,
                   count(*) filter (
                       where exists (
                           select 1
                           from run_results rr3
                           join events e3 on e3.id = rr3.event_id
                           where rr3.participant_id = d.participant_id
                             and e3.location_id = d.location_id
                             and rr3.finish_time_sec is not null
                             and e3.event_date > d.first_date
                       )
                   ) as returned
            from debuts d
            group by d.location_id
            having count(*) >= 5
            """
        ),
        {"cutoff": cutoff, "recent_cap": recent_cap},
    ).all()

    shares = [100.0 * returned / debuts for _location_id, debuts, returned in rows if debuts]
    value = round(median(shares), 1) if shares else None
    _write_json_cache(cache_key, {"median": value}, NETWORK_CACHE_TTL_SECONDS)
    return value
