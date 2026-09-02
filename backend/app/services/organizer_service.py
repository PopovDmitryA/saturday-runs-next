"""Инструменты кабинета организатора: «Долгая пауза» и список событий локации.

«Долгая пауза» — перенос одноимённого Grafana-дашборда на данные сайта: кто из
постоянных участников локации давно не появлялся. Постоянство меряется числом
пробежек на локации (порог min_runs), пауза — числом СОБЫТИЙ локации, прошедших
после последнего визита (порог min_missed), а не календарём: локация могла
пропускать субботы.

Отличия от легаси: локация — каноническая идентичность (все платформы точки),
человек склеен по платформам через platform_links (как в топах локации),
без привязки на сайте остаётся платформенный participant отдельной строкой.
"""

from __future__ import annotations

from datetime import date
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
    User,
    VolunteerResult,
)
from app.services.location_catalog_service import normalize_platform_code
from app.services.location_page_service import (
    LocationIdentity,
    _location_event_ids,
    _platform_link_join,
    _read_json_cache,
    _write_json_cache,
)

ABSENCE_CACHE_TTL_SECONDS = 3 * 60 * 60

ABSENCE_MIN_RUNS_DEFAULT = 10
ABSENCE_MIN_MISSED_DEFAULT = 4


def absence_cache_key(
    identity_key: str, min_runs: int, min_missed: int, current_only: bool
) -> str:
    return f"organizer:absence:v3:{identity_key}:{min_runs}:{min_missed}:{int(current_only)}"


def build_location_absence(
    db: Session,
    identity: LocationIdentity,
    *,
    min_runs: int = ABSENCE_MIN_RUNS_DEFAULT,
    min_missed: int = ABSENCE_MIN_MISSED_DEFAULT,
    current_only: bool = False,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = absence_cache_key(identity.identity_key, min_runs, min_missed, current_only)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_absence(
        db, identity, min_runs=min_runs, min_missed=min_missed, current_only=current_only
    )

    if use_cache:
        _write_json_cache(cache_key, payload, ABSENCE_CACHE_TTL_SECONDS)
    return payload


def _compute_location_absence(
    db: Session,
    identity: LocationIdentity,
    *,
    min_runs: int,
    min_missed: int,
    current_only: bool = False,
) -> dict[str, Any]:
    # «Только действующая система»: у площадки, пережившей parkrun, стаж
    # набран в основном там, и в списке висят люди, не приходившие с 2022 года.
    # Фильтр сужает расчёт до локаций той системы, в которой площадка работает
    # сейчас (просьба Дмитрия 03.09.2026). Выбирать систему руками смысла нет:
    # осмысленный вопрос всегда один — «а что у нас сейчас».
    active_code = (
        normalize_platform_code(identity.catalog.active_platform) if identity.catalog else None
    )
    members = identity.locations
    if current_only and active_code:
        current = [(loc, code) for loc, code in members if code == active_code]
        # Если действующей системы среди локаций нет (данные разъехались),
        # молча считаем по всем: пустой список дал бы пустую страницу.
        members = current or members
    location_ids = [location.id for location, _code in members]
    event_ids = _location_event_ids(db, location_ids)

    base: dict[str, Any] = {
        "location": {"slug": identity.slug, "name": identity.name},
        "min_runs": min_runs,
        "min_missed": min_missed,
        "current_only": current_only,
        "current_platform": active_code,
        "events_total": len(event_ids),
        "items": [],
        "total": 0,
    }
    if not event_ids:
        return base

    event_dates = sorted(
        {
            row[0]
            for row in db.query(Event.event_date).filter(Event.id.in_(event_ids)).all()
        }
    )

    group_key = func.coalesce(PlatformLink.user_id, RunResult.participant_id)
    display_name = func.max(func.coalesce(User.display_name, Participant.display_name))
    local_rows = (
        db.query(
            group_key.label("group_key"),
            display_name.label("name"),
            func.count(func.distinct(RunResult.event_id)).label("runs_here"),
            func.max(Event.event_date).label("last_date"),
            func.max(User.public_slug).label("slug"),
            func.max(User.serial_id).label("serial_id"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .outerjoin(User, PlatformLink.user_id == User.id)
        .filter(RunResult.event_id.in_(event_ids))
        .group_by(group_key)
        .having(func.count(func.distinct(RunResult.event_id)) >= min_runs)
        .all()
    )
    if not local_rows:
        return base

    # Всего пробежек (по всем локациям) — только для уже отобранных людей.
    # Дедуп RunPark-дублей здесь через NOT IN по кросслинкам: событий много,
    # IN-список всех event_id мира сюда не привезёшь.
    group_keys = [row.group_key for row in local_rows]
    secondary_events = select(EventCrosslink.secondary_event_id)
    total_rows = (
        db.query(
            group_key.label("group_key"),
            func.count(func.distinct(RunResult.event_id)).label("runs_total"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .filter(
            group_key.in_(group_keys),
            Event.is_test_event.is_(False),
            Event.id.notin_(secondary_events),
        )
        .group_by(group_key)
        .all()
    )
    totals = {row.group_key: int(row.runs_total) for row in total_rows}

    # Последний визит — это и волонтёрство тоже: человек, который перестал
    # бегать, но каждую субботу стоит на позиции, никуда не пропадал, и
    # показывать его в «долгой паузе» неправильно (Дмитрий 02.09.2026).
    # Отдельным запросом по волонтёрским строкам той же площадки, ключ
    # группировки тот же — профиль сайта либо участник системы.
    vol_group_key = func.coalesce(PlatformLink.user_id, VolunteerResult.participant_id)
    vol_last: dict[Any, date] = {
        row.group_key: row.last_date
        for row in (
            db.query(
                vol_group_key.label("group_key"),
                func.max(Event.event_date).label("last_date"),
            )
            # select_from обязателен: ключ группировки начинается с
            # platform_links, и без явного якоря SQLAlchemy делает её ведущей
            # таблицей — получается декартово произведение.
            .select_from(VolunteerResult)
            .join(Event, VolunteerResult.event_id == Event.id)
            .join(Participant, VolunteerResult.participant_id == Participant.id)
            .outerjoin(PlatformLink, _platform_link_join())
            .filter(
                VolunteerResult.event_id.in_(event_ids),
                vol_group_key.in_(group_keys),
                # Волонтёрства parkrun-эпохи лежат на дате-заглушке 1970-01-01
                # (сводка профиля, а не событие) — они не про «был здесь тогда-то».
                Event.event_date > date(1970, 1, 1),
            )
            .group_by(vol_group_key)
            .all()
        )
    }

    # Кому эта площадка — дом. Логика общесайтовая (ручной выбор плюс три
    # ступени), поэтому переиспользуем home_participant_ids: строка получает
    # отметку, а фильтровать по ней или нет — решает страница.
    participant_by_group: dict[Any, set[Any]] = {}
    for gkey, pid in (
        db.query(group_key.label("group_key"), Participant.id)
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .filter(RunResult.event_id.in_(event_ids), group_key.in_(group_keys))
        .distinct()
        .all()
    ):
        participant_by_group.setdefault(gkey, set()).add(pid)
    home_ids = home_participant_ids(
        db, identity.identity_key, {pid for pids in participant_by_group.values() for pid in pids}
    )

    items: list[dict[str, Any]] = []
    for row in local_rows:
        # Последний визит: позднейшая из двух дат — пробежки и волонтёрства.
        last_date: date = row.last_date
        volunteered = vol_last.get(row.group_key)
        if volunteered is not None and volunteered > last_date:
            last_date = volunteered
        missed = sum(1 for event_date in event_dates if event_date > last_date)
        if missed < min_missed:
            continue
        items.append(
            {
                "name": row.name,
                "handle": row.slug or (str(row.serial_id) if row.serial_id else None),
                "last_date": last_date.isoformat(),
                "last_date_display": last_date.strftime("%d.%m.%Y"),
                "runs_here": int(row.runs_here),
                "runs_total": totals.get(row.group_key, int(row.runs_here)),
                "missed_events": missed,
                # Своя площадка или заезжий: по этой отметке страница может
                # спрятать тех, кто забежал сюда однажды в путешествии.
                "is_home": bool(participant_by_group.get(row.group_key, set()) & home_ids),
            }
        )

    items.sort(key=lambda item: (-int(item["runs_here"]), str(item["name"] or "")))
    base["items"] = items
    base["total"] = len(items)
    return base


def list_identity_event_dates(db: Session, identity: LocationIdentity) -> list[dict[str, Any]]:
    """Даты событий идентичности (новые сверху) для селекта свода.

    Аналог list_report_event_dates из отчёта админки, но по всем платформам
    идентичности и с дедупом кросслинков.
    """
    location_ids = [location.id for location, _code in identity.locations]
    event_ids = _location_event_ids(db, location_ids)
    if not event_ids:
        return []

    finishers = (
        select(func.count())
        .select_from(RunResult)
        .where(RunResult.event_id == Event.id)
        .scalar_subquery()
    )
    rows = (
        db.query(
            Event.id,
            Event.event_date,
            Event.event_number,
            Platform.code,
            finishers.label("finishers_count"),
        )
        .join(Platform, Event.platform_id == Platform.id)
        .filter(Event.id.in_(event_ids))
        .order_by(Event.event_date.desc(), Platform.code)
        .all()
    )
    return [
        {
            "event_id": event_id,
            "event_date": event_date,
            "event_number": event_number,
            "platform_code": platform_code,
            "finishers_count": int(finishers_count or 0),
        }
        for event_id, event_date, event_number, platform_code, finishers_count in rows
    ]


# ===== Календарь юбилеев =====

MILESTONES_CACHE_TTL_SECONDS = 3 * 60 * 60
# Сколько стартов до юбилея показываем (юбилей «через 1..N участий»).
MILESTONE_HORIZON = 4
# Активные участники локации: были здесь за последние N дней. Без среза
# календарь забит теми, кто застыл в шаге от юбилея годы назад.
MILESTONE_ACTIVE_DAYS = 90


def _next_milestone(total: int) -> int:
    """Ближайший юбилей строго больше total: 10, дальше кратные 25."""
    if total < 10:
        return 10
    if total < 25:
        return 25
    return (total // 25 + 1) * 25


def milestones_cache_key(identity_key: str) -> str:
    return f"organizer:milestones:v1:{identity_key}"


def build_location_milestones(
    db: Session,
    identity: LocationIdentity,
    *,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = milestones_cache_key(identity.identity_key)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_milestones(db, identity)

    if use_cache:
        _write_json_cache(cache_key, payload, MILESTONES_CACHE_TTL_SECONDS)
    return payload


def _compute_location_milestones(db: Session, identity: LocationIdentity) -> dict[str, Any]:
    """Кто из активных участников локации в 1–4 участиях от юбилея.

    Обратная сторона свода: там юбилей виден уже после старта, здесь — заранее,
    чтобы оргкоманда успела подготовить поздравление. Счётчики платформенные
    (как в своде): у каждой системы своя юбилейная арифметика.
    """
    location_ids = [location.id for location, _code in identity.locations]
    event_ids = _location_event_ids(db, location_ids)

    base: dict[str, Any] = {
        "location": {"slug": identity.slug, "name": identity.name},
        "horizon": MILESTONE_HORIZON,
        "active_days": MILESTONE_ACTIVE_DAYS,
        "items": [],
        "total": 0,
    }
    if not event_ids:
        return base

    from datetime import date as date_type
    from datetime import timedelta

    cutoff = date_type.today() - timedelta(days=MILESTONE_ACTIVE_DAYS)

    def _local_counts(model: type[RunResult] | Any) -> dict[Any, tuple[int, date]]:
        rows = (
            db.query(
                model.participant_id,
                func.count(func.distinct(model.event_id)),
                func.max(Event.event_date),
            )
            .join(Event, model.event_id == Event.id)
            .filter(model.event_id.in_(event_ids), model.participant_id.isnot(None))
            .group_by(model.participant_id)
            .all()
        )
        return {pid: (int(count), last) for pid, count, last in rows}

    from app.models import VolunteerResult

    local_runs = _local_counts(RunResult)
    local_vols = _local_counts(VolunteerResult)

    active_ids = {
        pid
        for counts in (local_runs, local_vols)
        for pid, (_count, last) in counts.items()
        if last >= cutoff
    }
    if not active_ids:
        return base

    secondary_events = select(EventCrosslink.secondary_event_id)

    def _platform_counts(model: type[Any], *, timed: bool) -> dict[Any, int]:
        query = (
            db.query(model.participant_id, func.count(func.distinct(model.event_id)))
            .join(Event, model.event_id == Event.id)
            .filter(
                model.participant_id.in_(active_ids),
                Event.is_test_event.is_(False),
                Event.id.notin_(secondary_events),
            )
        )
        if timed:
            # Как в своде: пробежки считаем по строкам со временем финиша.
            query = query.filter(model.finish_time_sec.isnot(None))
        return {pid: int(count) for pid, count in query.group_by(model.participant_id).all()}

    platform_runs = _platform_counts(RunResult, timed=True)
    platform_vols = _platform_counts(VolunteerResult, timed=False)

    people = {
        row.id: row
        for row in db.query(Participant).filter(Participant.id.in_(active_ids)).all()
    }

    KINDS = (
        ("runs_here", "Пробежки здесь", local_runs, True),
        ("runs_platform", "Пробежки в системе", platform_runs, False),
        ("vols_here", "Волонтёрства здесь", local_vols, True),
        ("vols_platform", "Волонтёрства в системе", platform_vols, False),
    )

    items: list[dict[str, Any]] = []
    for pid in active_ids:
        participant = people.get(pid)
        if participant is None:
            continue
        last_seen = max(
            (counts[pid][1] for counts in (local_runs, local_vols) if pid in counts),
            default=None,
        )
        for kind, kind_label, counts, local in KINDS:
            raw = counts.get(pid)
            if raw is None:
                continue
            count = raw[0] if local else raw
            if count == 0:
                continue
            milestone = _next_milestone(count)
            remaining = milestone - count
            if remaining > MILESTONE_HORIZON:
                continue
            items.append(
                {
                    "participant_id": str(pid),
                    "name": participant.display_name,
                    "profile_url": participant.profile_url,
                    "kind": kind,
                    "kind_label": kind_label,
                    "current": count,
                    "milestone": milestone,
                    "remaining": remaining,
                    "last_seen": last_seen.isoformat() if last_seen else None,
                    "last_seen_display": last_seen.strftime("%d.%m.%Y") if last_seen else None,
                }
            )

    items.sort(key=lambda item: (item["remaining"], -int(item["milestone"]), str(item["name"] or "")))
    base["items"] = items
    base["total"] = len(items)
    return base


# ===== Удержание новичков =====

NEWCOMERS_CACHE_TTL_SECONDS = 3 * 60 * 60
NEWCOMERS_DEFAULT_DAYS = 180


def newcomers_cache_key(identity_key: str, days: int) -> str:
    return f"organizer:newcomers:v1:{identity_key}:{days}"


def build_location_newcomers(
    db: Session,
    identity: LocationIdentity,
    *,
    days: int = NEWCOMERS_DEFAULT_DAYS,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = newcomers_cache_key(identity.identity_key, days)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_newcomers(db, identity, days=days)

    if use_cache:
        _write_json_cache(cache_key, payload, NEWCOMERS_CACHE_TTL_SECONDS)
    return payload


def _compute_location_newcomers(
    db: Session, identity: LocationIdentity, *, days: int
) -> dict[str, Any]:
    """Дебютанты системы на этой локации и вернулись ли они.

    Новичок — по определению Дмитрия (16.08.2026): не «гость с другой локации»,
    а тот, чья ПЕРВАЯ пробежка в системе вообще случилась здесь. Удержание —
    вернулся ли он бегать ещё раз; считаем отдельно «вернулся сюда» и «бегает
    где-то» (уехавший в другую локацию для системы не потерян, но для этой
    локации — да).

    В процент удержания не входят дебютанты последнего события: у них ещё не
    было субботы, чтобы вернуться.
    """
    location_ids = [location.id for location, _code in identity.locations]
    event_ids = _location_event_ids(db, location_ids)

    base: dict[str, Any] = {
        "location": {"slug": identity.slug, "name": identity.name},
        "days": days,
        "items": [],
        "total": 0,
        "eligible_total": 0,
        "returned_here_total": 0,
        "retention_pct": None,
    }
    if not event_ids:
        return base

    from datetime import date as date_type
    from datetime import timedelta

    cutoff = date_type.today() - timedelta(days=days)
    secondary_events = select(EventCrosslink.secondary_event_id)

    # Первая пробежка каждого участника в его системе (не-тест, без дублей,
    # только опознанные строки со временем — как везде в счётчиках).
    timed = RunResult.finish_time_sec.isnot(None)
    firsts = (
        db.query(
            RunResult.participant_id.label("pid"),
            func.min(Event.event_date).label("first_date"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id.isnot(None),
            timed,
            Event.is_test_event.is_(False),
            Event.id.notin_(secondary_events),
        )
        .group_by(RunResult.participant_id)
        .subquery()
    )
    # Дебют здесь: строка первой даты участника лежит на событии этой локации.
    debut_rows = (
        db.query(
            RunResult.participant_id,
            firsts.c.first_date,
            Participant.display_name,
            Participant.profile_url,
        )
        .join(firsts, firsts.c.pid == RunResult.participant_id)
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .filter(
            Event.event_date == firsts.c.first_date,
            Event.location_id.in_(location_ids),
            RunResult.event_id.in_(event_ids),
            timed,
            firsts.c.first_date >= cutoff,
        )
        .distinct()
        .all()
    )
    if not debut_rows:
        return base

    novice_ids = [row[0] for row in debut_rows]

    here_rows = (
        db.query(
            RunResult.participant_id,
            func.count(func.distinct(RunResult.event_id)),
            func.max(Event.event_date),
        )
        .join(Event, RunResult.event_id == Event.id)
        .filter(RunResult.participant_id.in_(novice_ids), RunResult.event_id.in_(event_ids), timed)
        .group_by(RunResult.participant_id)
        .all()
    )
    here = {pid: (int(count), last) for pid, count, last in here_rows}

    anywhere_rows = (
        db.query(
            RunResult.participant_id,
            func.count(func.distinct(RunResult.event_id)),
            func.max(Event.event_date),
        )
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id.in_(novice_ids),
            timed,
            Event.is_test_event.is_(False),
            Event.id.notin_(secondary_events),
        )
        .group_by(RunResult.participant_id)
        .all()
    )
    anywhere = {pid: (int(count), last) for pid, count, last in anywhere_rows}

    last_event_date = max(
        row[0] for row in db.query(Event.event_date).filter(Event.id.in_(event_ids)).all()
    )

    items: list[dict[str, Any]] = []
    eligible = 0
    returned_here_count = 0
    for pid, first_date, name, profile_url in debut_rows:
        runs_here, last_here = here.get(pid, (1, first_date))
        runs_total, last_any = anywhere.get(pid, (1, first_date))
        returned_here = runs_here > 1
        # Дебютант последнего события ещё не имел шанса вернуться.
        is_eligible = first_date < last_event_date
        if is_eligible:
            eligible += 1
            if returned_here:
                returned_here_count += 1
        items.append(
            {
                "participant_id": str(pid),
                "name": name,
                "profile_url": profile_url,
                "debut_date": first_date.isoformat(),
                "debut_date_display": first_date.strftime("%d.%m.%Y"),
                "runs_here": runs_here,
                "runs_total": runs_total,
                "last_here_display": last_here.strftime("%d.%m.%Y") if last_here else None,
                "last_anywhere_display": last_any.strftime("%d.%m.%Y") if last_any else None,
                "returned_here": returned_here,
                "runs_elsewhere": max(runs_total - runs_here, 0),
            }
        )

    items.sort(key=lambda item: str(item["debut_date"]), reverse=True)
    base["items"] = items
    base["total"] = len(items)
    base["eligible_total"] = eligible
    base["returned_here_total"] = returned_here_count
    base["retention_pct"] = round(returned_here_count / eligible * 100) if eligible else None
    return base


# ===== Волонтёрская скамейка =====

BENCH_CACHE_TTL_SECONDS = 3 * 60 * 60
# Сколько подряд пропущенных событий локации делают волонтёра «выпавшим».
# Локации проводят старты еженедельно — 10 событий это примерно 2,5 месяца.
BENCH_PAUSE_EVENTS = 10
# Порог «своих» бегунов, попадающих в скамейку без единого волонтёрства.
BENCH_MIN_RUNS_DEFAULT = 5


def bench_cache_key(identity_key: str, min_runs: int) -> str:
    return f"organizer:bench:v5:{identity_key}:{min_runs}"


def build_location_volunteer_bench(
    db: Session,
    identity: LocationIdentity,
    *,
    min_runs: int = BENCH_MIN_RUNS_DEFAULT,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = bench_cache_key(identity.identity_key, min_runs)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_volunteer_bench(db, identity, min_runs=min_runs)

    if use_cache:
        _write_json_cache(cache_key, payload, BENCH_CACHE_TTL_SECONDS)
    return payload


def _compute_location_volunteer_bench(
    db: Session, identity: LocationIdentity, *, min_runs: int
) -> dict[str, Any]:
    """Скамейка локации: кого позвать волонтёрить и кто давно не выходил.

    Переосмыслено 18.08.2026 (Дмитрий): главная ценность раздела — НЕ список
    заслуженных ветеранов, а подсказка «кого звать». Поэтому в выдачу входят и
    те, кто здесь только бегает и ни разу не помогал: именно они наверху.

    Статус участника:
    * never  — бегает здесь (от min_runs финишей), волонтёрств тут нет вообще;
    * paused — волонтёрил, но пропустил BENCH_PAUSE_EVENTS событий подряд;
    * active — выходил недавно, трогать не нужно.

    «Кандидат» = never, либо paused, который ПРОДОЛЖАЕТ здесь бегать (есть
    финиши после последнего волонтёрства): такого человека реально дозваться.
    """
    from app.models import VolunteerResult
    from app.volunteer_role_taxonomy import canonical_volunteer_role, strip_role_counters

    location_ids = [location.id for location, _code in identity.locations]
    event_ids = _location_event_ids(db, location_ids)

    base: dict[str, Any] = {
        "location": {"slug": identity.slug, "name": identity.name},
        "events_total": len(event_ids),
        "min_runs": min_runs,
        "pause_events": BENCH_PAUSE_EVENTS,
        "items": [],
        "total": 0,
        "candidates_total": 0,
    }
    if not event_ids:
        return base

    event_dates = sorted(
        {row[0] for row in db.query(Event.event_date).filter(Event.id.in_(event_ids)).all()}
    )

    # --- Волонтёрства на локации: роли, даты, последний выход ---
    vol_rows = (
        db.query(
            VolunteerResult.participant_id,
            VolunteerResult.role,
            VolunteerResult.event_id,
            Event.event_date,
        )
        .join(Event, VolunteerResult.event_id == Event.id)
        .filter(VolunteerResult.event_id.in_(event_ids))
        .all()
    )
    vol_events: dict[Any, set[Any]] = {}
    last_vol: dict[Any, date] = {}
    role_events: dict[Any, dict[str, set[Any]]] = {}
    role_labels: dict[str, str] = {}
    for pid, role, event_id, event_date in vol_rows:
        vol_events.setdefault(pid, set()).add(event_id)
        if pid not in last_vol or event_date > last_vol[pid]:
            last_vol[pid] = event_date
        canonical = canonical_volunteer_role(role)
        if canonical is not None:
            role_events.setdefault(pid, {}).setdefault(canonical.key, set()).add(event_id)
            # Ярлык — как в системе (у 5в «Организатор», не «Директор забега»):
            # канонический ключ группирует, показываем сырое название без счётчиков.
            role_labels.setdefault(canonical.key, strip_role_counters(role))

    # --- Пробежки на локации: нужны и даты, чтобы понять «бегает ли сейчас» ---
    run_rows = (
        db.query(RunResult.participant_id, Event.event_date)
        .join(Event, RunResult.event_id == Event.id)
        .filter(RunResult.event_id.in_(event_ids), RunResult.participant_id.isnot(None))
        .all()
    )
    run_dates: dict[Any, list[date]] = {}
    for pid, event_date in run_rows:
        run_dates.setdefault(pid, []).append(event_date)

    # Кто попадает в выдачу: все волонтёры локации + бегуны от min_runs финишей.
    runner_ids = {pid for pid, dates in run_dates.items() if len(set(dates)) >= min_runs}
    pids = set(vol_events) | runner_ids
    if not pids:
        return base

    people = {
        row.id: row for row in db.query(Participant).filter(Participant.id.in_(pids)).all()
    }
    secondary_events = select(EventCrosslink.secondary_event_id)
    totals = {
        pid: int(count)
        for pid, count in (
            db.query(
                VolunteerResult.participant_id,
                func.count(func.distinct(VolunteerResult.event_id)),
            )
            .join(Event, VolunteerResult.event_id == Event.id)
            .filter(
                VolunteerResult.participant_id.in_(pids),
                Event.is_test_event.is_(False),
                Event.id.notin_(secondary_events),
            )
            .group_by(VolunteerResult.participant_id)
            .all()
        )
    }

    items: list[dict[str, Any]] = []
    for pid in pids:
        participant = people.get(pid)
        if participant is None:
            continue
        dates_here = sorted(set(run_dates.get(pid, [])))
        runs_here = len(dates_here)
        last_run = dates_here[-1] if dates_here else None
        vols_here = len(vol_events.get(pid, ()))
        vol_date = last_vol.get(pid)

        if vol_date is None:
            status = "never"
            missed = None
            runs_after_vol = runs_here
        else:
            missed = sum(1 for event_date in event_dates if event_date > vol_date)
            runs_after_vol = sum(1 for event_date in dates_here if event_date > vol_date)
            status = "paused" if missed >= BENCH_PAUSE_EVENTS else "active"

        is_candidate = status == "never" or (status == "paused" and runs_after_vol > 0)
        roles = sorted(
            (
                {"label": role_labels[key], "count": len(events)}
                for key, events in role_events.get(pid, {}).items()
            ),
            key=lambda role: (-role["count"], role["label"]),
        )
        items.append(
            {
                "participant_id": str(pid),
                "name": participant.display_name,
                "profile_url": participant.profile_url,
                "vols_here": vols_here,
                "vols_total": totals.get(pid, vols_here),
                "runs_here": runs_here,
                "runs_after_last_vol": runs_after_vol,
                "last_vol_date": vol_date.isoformat() if vol_date else None,
                "last_vol_display": vol_date.strftime("%d.%m.%Y") if vol_date else None,
                "missed_events": missed,
                "roles": roles,
                "last_run_date": last_run.isoformat() if last_run else None,
                "last_run_display": last_run.strftime("%d.%m.%Y") if last_run else None,
                "status": status,
                "is_candidate": is_candidate,
            }
        )

    # Порядок по умолчанию — «кого звать первым»: сначала те, кто много бегает
    # и ни разу не помогал, затем вернувшиеся к бегу после паузы в волонтёрстве,
    # и только потом действующая команда.
    status_rank = {"never": 0, "paused": 1, "active": 2}
    items.sort(
        key=lambda item: (
            0 if item["is_candidate"] else 1,
            status_rank[item["status"]],
            -int(item["runs_after_last_vol"] or 0) if item["is_candidate"] else 0,
            -int(item["vols_here"]),
            str(item["name"] or ""),
        )
    )
    base["items"] = items
    base["total"] = len(items)
    base["candidates_total"] = sum(1 for item in items if item["is_candidate"])
    return base


# ===== «Наши в гостях» =====

# Кого считаем «своим»: дефолт порога финишей на локации (настраивается).
TRAVELERS_LOCAL_MIN_RUNS = 5


def list_event_travelers(
    db: Session,
    identity: LocationIdentity,
    event_date: date,
    *,
    min_runs: int = TRAVELERS_LOCAL_MIN_RUNS,
) -> list[dict[str, Any]]:
    """Свои участники локации, бегавшие в эту дату в других парках.

    «Свой» (уточнение Дмитрия 17.08.2026) — двойное условие: от min_runs
    финишей здесь И наша локация для него ДОМАШНЯЯ по общесайтовой логике
    (как в рейтинге дальности от дома): у привязанных к сайту — ручной выбор
    дома либо три ступени автоотбора home_location_service; у непривязанных —
    те же три ступени по их платформенным данным (пробежки → волонтёрства →
    самая ранняя пробежка, всё за всю историю). Гость, который набегал у нас
    пять стартов, но живёт в другом парке, в рубрику не попадает.

    Participant платформенный, поэтому фильтр по системе не нужен; дубли
    RunPark отсекаются кросслинками.
    """
    location_ids = [location.id for location, _code in identity.locations]
    event_ids = _location_event_ids(db, location_ids)
    if not event_ids:
        return []

    locals_sq = (
        select(RunResult.participant_id)
        .where(RunResult.event_id.in_(event_ids), RunResult.participant_id.isnot(None))
        .group_by(RunResult.participant_id)
        .having(func.count(func.distinct(RunResult.event_id)) >= min_runs)
    )
    secondary_events = select(EventCrosslink.secondary_event_id)
    rows = (
        db.query(
            RunResult.participant_id,
            Participant.display_name,
            Participant.profile_url,
            Location.name,
            Location.city,
            RunResult.finish_time_sec,
        )
        .join(RunResult, RunResult.participant_id == Participant.id)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            Event.event_date == event_date,
            Event.is_test_event.is_(False),
            Event.id.notin_(secondary_events),
            Event.location_id.notin_(location_ids),
            RunResult.participant_id.in_(locals_sq),
            RunResult.finish_time_sec.isnot(None),
        )
        .order_by(Location.name, Participant.display_name)
        .all()
    )
    if not rows:
        return []

    candidate_ids = {row[0] for row in rows}
    home_ids = home_participant_ids(db, identity.identity_key, candidate_ids)
    if not home_ids:
        return []

    # Сколько раз бежал у нас — на дату события, а не «сегодня»: пост про
    # конкретную субботу, и для старого события число не должно ехать вперёд.
    runs_here = {
        pid: int(count)
        for pid, count in (
            db.query(RunResult.participant_id, func.count(func.distinct(RunResult.event_id)))
            .join(Event, RunResult.event_id == Event.id)
            .filter(
                RunResult.participant_id.in_(home_ids),
                RunResult.event_id.in_(event_ids),
                Event.event_date <= event_date,
            )
            .group_by(RunResult.participant_id)
            .all()
        )
    }

    return [
        {
            "name": name,
            "profile_url": profile_url,
            "away_location": away_name,
            "away_city": away_city,
            "finish_time_sec": int(finish_time_sec) if finish_time_sec else None,
            "runs_here": runs_here.get(pid, 0),
        }
        for pid, name, profile_url, away_name, away_city, finish_time_sec in rows
        if pid in home_ids
    ]


def home_participant_ids(
    db: Session, identity_key: str, candidate_ids: set[Any]
) -> set[Any]:
    """Участники, для которых идентичность identity_key — домашняя локация.

    Используется рубрикой «Наши в гостях» и гейтом «шага до юбилея» в своде."""
    homes = participant_home_keys(db, candidate_ids)
    return {pid for pid, key in homes.items() if key == identity_key}


def participant_home_keys(db: Session, candidate_ids: set[Any]) -> dict[Any, str]:
    """Домашняя локация каждого участника: pid → canonical identity key.

    Общесайтовая логика дома (как в рейтинге дальности): у привязанных к сайту
    — resolve_home_location (ручной выбор + три ступени, кросс-платформенно);
    у непривязанных — те же три ступени по их платформенным данным. Участники
    без определившегося дома в ответ не попадают.
    Используется и «Экспресс-итогом» («откуда гости»)."""
    from app.services.home_location_service import (
        HomeLocationCandidate,
        _auto_home_location,
        home_location_candidates_from_detail,
        resolve_home_location_from_candidates,
    )
    from app.services.location_catalog_service import LocationCatalogIndex
    from app.services.user_unique_locations_detail import build_user_unique_location_details

    linked_rows = (
        db.query(Participant.id, PlatformLink.user_id)
        .join(PlatformLink, _platform_link_join())
        .filter(Participant.id.in_(candidate_ids))
        .all()
    )
    pid_to_user = {pid: user_id for pid, user_id in linked_rows}
    homes: dict[Any, str] = {}

    # Индекс каталога — ОДИН на весь отчёт: его загрузка тянет все локации и
    # связки каталога, и когда resolve_home_location строил его заново на
    # каждого привязанного участника, отчёт большой локации превращался в
    # десятки полных загрузок каталога за один запрос.
    catalog_index = LocationCatalogIndex(db)

    # Привязанные: общесайтовый резолвер по пользователю (ручной выбор + три
    # ступени), но с общим catalog_index вместо построения своего на каждого.
    users_by_id = {
        user.id: user
        for user in db.query(User).filter(User.id.in_(set(pid_to_user.values()))).all()
    }
    user_home: dict[Any, str | None] = {}
    for user_id, user in users_by_id.items():
        candidates = home_location_candidates_from_detail(
            build_user_unique_location_details(db, user_id, catalog_index=catalog_index)
        )
        candidate, _is_auto = resolve_home_location_from_candidates(candidates, user)
        user_home[user_id] = candidate.catalog_identity_key if candidate else None
    for pid, user_id in pid_to_user.items():
        key = user_home.get(user_id)
        if key:
            homes[pid] = key

    unlinked = candidate_ids - set(pid_to_user)
    if not unlinked:
        return homes

    # Непривязанные: собираем те же ступени по платформенным данным участника.
    secondary_events = select(EventCrosslink.secondary_event_id)

    def _identity_of(location: Location, platform_code: str) -> str:
        return catalog_index.canonical_identity_key(location, platform_code)

    run_rows = (
        db.query(
            RunResult.participant_id,
            Location,
            Platform.code,
            func.count(func.distinct(RunResult.event_id)),
            func.min(Event.event_date),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(
            RunResult.participant_id.in_(unlinked),
            RunResult.finish_time_sec.isnot(None),
            Event.is_test_event.is_(False),
            Event.id.notin_(secondary_events),
        )
        .group_by(RunResult.participant_id, Location.id, Platform.code)
        .all()
    )
    from app.models import VolunteerResult

    vol_rows = (
        db.query(
            VolunteerResult.participant_id,
            Location,
            Platform.code,
            func.count(func.distinct(VolunteerResult.event_id)),
        )
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(
            VolunteerResult.participant_id.in_(unlinked),
            Event.is_test_event.is_(False),
            Event.id.notin_(secondary_events),
        )
        .group_by(VolunteerResult.participant_id, Location.id, Platform.code)
        .all()
    )

    per_participant: dict[Any, dict[str, dict[str, Any]]] = {}
    for pid, location, code, count, first_date in run_rows:
        key = _identity_of(location, code)
        entry = per_participant.setdefault(pid, {}).setdefault(
            key, {"runs": 0, "vols": 0, "first": None}
        )
        entry["runs"] += int(count)
        first_iso = first_date.isoformat() if first_date else None
        if first_iso and (entry["first"] is None or first_iso < entry["first"]):
            entry["first"] = first_iso
    for pid, location, code, count in vol_rows:
        key = _identity_of(location, code)
        entry = per_participant.setdefault(pid, {}).setdefault(
            key, {"runs": 0, "vols": 0, "first": None}
        )
        entry["vols"] += int(count)

    for pid in unlinked:
        identities = per_participant.get(pid)
        if not identities:
            continue
        candidates = [
            HomeLocationCandidate(
                catalog_identity_key=key,
                name=key,
                city=None,
                region=None,
                run_count=entry["runs"],
                volunteer_count=entry["vols"],
                platform_codes=[],
                first_run_date=entry["first"],
            )
            for key, entry in identities.items()
        ]
        winner = _auto_home_location(candidates)
        if winner is not None:
            homes[pid] = winner.catalog_identity_key
    return homes
