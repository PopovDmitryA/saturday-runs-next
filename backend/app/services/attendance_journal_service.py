from __future__ import annotations

"""Журналы посещаемости поверх рейтингов: перенос Grafana-дашбордов
«Журнал посещаемости пробежек», «Журнал туризма» и волонтёрского аналога.

Журнал — режим «Журнал» на странице рейтинга: те же строки и порядок, что в
таблице рейтинга (снапшот лидерборда), но вместо колонок систем — визиты по
датам выбранного года. Стро­ки берутся из снапшота (get_leaderboard_snapshot),
даты — точечным SQL по участникам страницы, тем же приёмом, что карта
туристов (_tourist_row_participants + запрос по :pids).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import User
from app.services.leaderboard_service import (
    CACHE_KEY_PREFIX,
    _location_identity_maps,
    _read_raw_cache,
    _row_key,
    _site_links,
    _tourist_row_participants,
    _write_raw_cache,
    get_leaderboard_snapshot,
    platform_filter_values,
)
from app.volunteer_role_taxonomy import canonical_volunteer_role
from app.volunteering_occasions import count_volunteering_occasions

# Метрики, у которых есть режим «Журнал». Волонтёрский журнал — новый (в
# Grafana его не было), появляется по аналогии с журналом пробежек.
JOURNAL_METRICS: tuple[str, ...] = ("runs", "volunteering", "locations")

JOURNAL_PAGE_LIMIT = 50
JOURNAL_MAX_LIMIT = 100

# Свой префикс внутри пространства лидербордов: снапшоты чистятся вместе
# с журналами одним drop_metric_cache-паттерном по CACHE_KEY_PREFIX.
_JOURNAL_CACHE_VERSION = "j1"

_YEARS_SQL = """
SELECT DISTINCT EXTRACT(YEAR FROM event_date)::int AS year
FROM events
WHERE is_test_event = false
ORDER BY year DESC
"""

# Пробежки страницы журнала: каждая строка run_results — один визит (как в
# _RUNS_SQL рейтинга, с тем же дедупом кросслинков). Фильтр parkrun-допуска не
# нужен: участники уже прошли его при попадании в снапшот.
_JOURNAL_RUNS_SQL = """
SELECT
    rr.participant_id AS participant_id,
    e.event_date AS event_date,
    e.location_id AS location_id,
    p.code AS platform_code
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND rr.participant_id = ANY(:pids)
  AND ec.secondary_event_id IS NULL
  AND e.event_date >= :year_start
  AND e.event_date < :year_end
  AND (:platform = 'all' OR p.code = :platform)
"""

# Волонтёрства с датами. parkrun здесь не участвует: его волонтёрства — сводка
# кредитов профиля без даты и локации, в журнал по датам им лечь некуда.
_JOURNAL_VOLUNTEERING_SQL = """
SELECT
    vr.participant_id AS participant_id,
    e.event_date AS event_date,
    e.location_id AS location_id,
    p.code AS platform_code,
    vr.role AS role
FROM volunteer_results vr
JOIN events e ON e.id = vr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND vr.participant_id = ANY(:pids)
  AND ec.secondary_event_id IS NULL
  AND p.code <> 'parkrun'
  AND e.event_date >= :year_start
  AND e.event_date < :year_end
  AND (:platform = 'all' OR p.code = :platform)
"""

# Первый визит участника на каждую площадку за ВСЮ историю — по нему журнал
# туризма отличает «новую локацию» от повтора внутри выбранного года.
_JOURNAL_FIRST_VISITS_SQL = """
SELECT
    rr.participant_id AS participant_id,
    e.location_id AS location_id,
    MIN(e.event_date) AS first_date
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND rr.participant_id = ANY(:pids)
  AND ec.secondary_event_id IS NULL
  AND (:platform = 'all' OR p.code = :platform)
GROUP BY rr.participant_id, e.location_id
"""


@dataclass
class _JournalEntry:
    """Одна отметка журнала: дата × площадка (× роль у волонтёрств)."""

    date: date
    location_id: UUID | None
    platform_code: str
    role: str | None = None


@dataclass
class _JournalRowDraft:
    entries: list[_JournalEntry] = field(default_factory=list)


def journal_years(db: Session) -> list[int]:
    return [int(row[0]) for row in db.execute(text(_YEARS_SQL)).all()]


def _year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year + 1, 1, 1)


def _collect_entries(
    db: Session,
    metric: str,
    pid_to_row: dict[UUID, str],
    year: int,
    platform: str,
) -> dict[str, _JournalRowDraft]:
    year_start, year_end = _year_bounds(year)
    params = {
        "pids": list(pid_to_row),
        "year_start": year_start,
        "year_end": year_end,
        "platform": platform,
    }
    sql = _JOURNAL_VOLUNTEERING_SQL if metric == "volunteering" else _JOURNAL_RUNS_SQL
    drafts: dict[str, _JournalRowDraft] = {}
    for row in db.execute(text(sql), params).all():
        if metric == "volunteering":
            pid, event_date, location_id, code, role = row
        else:
            pid, event_date, location_id, code = row
            role = None
        row_key = pid_to_row.get(pid)
        if row_key is None:
            continue
        drafts.setdefault(row_key, _JournalRowDraft()).entries.append(
            _JournalEntry(date=event_date, location_id=location_id, platform_code=code, role=role)
        )
    return drafts


def _first_visit_dates(
    db: Session,
    pid_to_row: dict[UUID, str],
    identity_by_location: dict[UUID, str],
    platform: str,
) -> dict[tuple[str, str], date]:
    """(row_key, identity площадки) -> дата первого визита за всю историю.

    Минимум берётся поверх всех участников строки: у зарегистрированного
    площадка «открыта» в тот день, когда он впервые был там ЛЮБОЙ из своих
    привязанных систем.
    """
    params = {"pids": list(pid_to_row), "platform": platform}
    first: dict[tuple[str, str], date] = {}
    for pid, location_id, first_date in db.execute(text(_JOURNAL_FIRST_VISITS_SQL), params).all():
        row_key = pid_to_row.get(pid)
        if row_key is None or location_id is None:
            continue
        identity = identity_by_location.get(location_id, str(location_id))
        key = (row_key, identity)
        current = first.get(key)
        if current is None or first_date < current:
            first[key] = first_date
    return first


def _volunteering_year_total(entries: list[_JournalEntry]) -> int:
    """Зачёт волонтёрств за год — той же логикой, что в рейтинге: у 5 вёрст и
    RunPark один зачёт на день (1 января — на каждую локацию), у S95 каждая
    запись считается отдельно."""
    occasion_rows: list[tuple[date, str]] = []
    s95_count = 0
    for entry in entries:
        if entry.platform_code == "s95":
            s95_count += 1
        else:
            occasion_rows.append((entry.date, str(entry.location_id or "unknown")))
    return s95_count + count_volunteering_occasions(occasion_rows)


def _role_label(role: str | None) -> str | None:
    canonical = canonical_volunteer_role(role)
    return canonical.label if canonical is not None else None


def _row_items(
    metric: str,
    row_key: str,
    draft: _JournalRowDraft,
    identity_by_location: dict[UUID, str],
    identity_names: dict[str, str],
    identity_slugs: dict[str, str],
    first_visits: dict[tuple[str, str], date],
) -> tuple[list[dict[str, object]], int]:
    """Отметки строки для витрины + «Всего» за год в единицах метрики."""
    items: list[dict[str, object]] = []
    new_count = 0
    seen: set[tuple[date, str, str, str]] = set()
    for entry in sorted(draft.entries, key=lambda e: e.date, reverse=True):
        identity = (
            identity_by_location.get(entry.location_id, str(entry.location_id))
            if entry.location_id is not None
            else ""
        )
        role_label = _role_label(entry.role) if metric == "volunteering" else None
        # Дубли одной отметки (задвоенные строки протокола) журналу не нужны.
        dedupe_key = (entry.date, identity, entry.platform_code, role_label or "")
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        item: dict[str, object] = {
            "date": entry.date.isoformat(),
            "location": identity_names.get(identity),
            "slug": identity_slugs.get(identity),
            "platform": entry.platform_code,
        }
        if role_label is not None:
            item["role"] = role_label
        if metric == "locations":
            is_new = first_visits.get((row_key, identity)) == entry.date
            item["new"] = is_new
            if is_new:
                new_count += 1
        items.append(item)

    if metric == "volunteering":
        year_total = _volunteering_year_total(draft.entries)
    elif metric == "locations":
        # «Всего» журнала туризма — новые площадки года: сумма по годам сходится
        # с общим зачётом рейтинга (уникальные площадки за всю историю).
        year_total = new_count
    else:
        year_total = len(items)
    return items, year_total


def _private_user_ids(db: Session, entity_keys: Sequence[str]) -> set[UUID]:
    user_ids: list[UUID] = []
    for key in entity_keys:
        prefix, _, raw = key.partition(":")
        if prefix != "u":
            continue
        try:
            user_ids.append(UUID(raw))
        except ValueError:
            continue
    if not user_ids:
        return set()
    rows = (
        db.query(User.id)
        .filter(User.id.in_(user_ids), User.profile_private.is_(True))
        .all()
    )
    return {row[0] for row in rows}


def _journal_cache_key(metric: str, year: int, platform: str, offset: int, limit: int) -> str:
    return (
        f"{CACHE_KEY_PREFIX}:journal:{_JOURNAL_CACHE_VERSION}:"
        f"{metric}:{year}:p{platform}:o{offset}:l{limit}"
    )


def _build_journal_rows(
    db: Session,
    metric: str,
    entity_keys: list[str],
    snapshot_rows: list[dict[str, object]],
    year: int,
    platform: str,
) -> list[dict[str, object]]:
    pid_to_row = _tourist_row_participants(db, entity_keys)
    drafts = _collect_entries(db, metric, pid_to_row, year, platform)
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    first_visits = (
        _first_visit_dates(db, pid_to_row, identity_by_location, platform)
        if metric == "locations"
        else {}
    )
    private_ids = _private_user_ids(db, entity_keys)

    rows: list[dict[str, object]] = []
    for entity_key, snapshot_row in zip(entity_keys, snapshot_rows, strict=False):
        row_key = cast("str", snapshot_row.get("row_key") or _row_key(entity_key))
        prefix, _, raw = entity_key.partition(":")
        is_private = False
        if prefix == "u":
            try:
                is_private = UUID(raw) in private_ids
            except ValueError:
                is_private = False
        draft = drafts.get(row_key, _JournalRowDraft())
        items, year_total = _row_items(
            metric,
            row_key,
            draft,
            identity_by_location,
            identity_names,
            identity_slugs,
            first_visits,
        )
        row: dict[str, object] = {
            "row_key": row_key,
            "rank": snapshot_row.get("rank"),
            "display_name": snapshot_row.get("display_name"),
            "site_serial_id": snapshot_row.get("site_serial_id"),
            "total": snapshot_row.get("total"),
            "year_total": year_total,
            "private": is_private,
            # Закрытый профиль оставляет в журнале счёт года, но не клетки:
            # по-датные визиты — ровно то, что человек попросил не собирать
            # в одном месте.
            "items": [] if is_private else items,
        }
        rows.append(row)
    return rows


def _viewer_journal_row(
    db: Session,
    metric: str,
    viewer: User,
    snapshot: dict[str, object],
    year: int,
    platform: str,
) -> dict[str, object] | None:
    entity_key = f"u:{viewer.id}"
    links = _site_links(db)
    pids = [pid for pid, link in links.items() if link.user_id == viewer.id]
    if not pids:
        return None
    row_key = _row_key(entity_key)
    pid_to_row = {pid: row_key for pid in pids}
    drafts = _collect_entries(db, metric, pid_to_row, year, platform)
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    first_visits = (
        _first_visit_dates(db, pid_to_row, identity_by_location, platform)
        if metric == "locations"
        else {}
    )
    draft = drafts.get(row_key, _JournalRowDraft())
    items, year_total = _row_items(
        metric, row_key, draft, identity_by_location, identity_names, identity_slugs, first_visits
    )

    rank: int | None = None
    total: int | None = None
    entity_keys = cast("list[str]", snapshot.get("entity_keys") or [])
    snapshot_rows = cast("list[dict[str, object]]", snapshot.get("rows") or [])
    if entity_key in entity_keys:
        index = entity_keys.index(entity_key)
        if index < len(snapshot_rows):
            rank = cast("int | None", snapshot_rows[index].get("rank"))
            total = cast("int | None", snapshot_rows[index].get("total"))
    return {
        "row_key": row_key,
        "rank": rank,
        "display_name": viewer.display_name,
        "site_serial_id": int(viewer.serial_id) if viewer.serial_id is not None else None,
        "total": total,
        "year_total": year_total,
        "private": False,
        "items": items,
    }


def get_attendance_journal(
    db: Session,
    metric: str,
    *,
    year: int | None = None,
    platform: str = "all",
    offset: int = 0,
    limit: int = JOURNAL_PAGE_LIMIT,
    viewer: User | None = None,
    use_cache: bool = True,
) -> dict[str, object]:
    if metric not in JOURNAL_METRICS:
        raise ValueError(f"У рейтинга {metric} нет журнала")
    limit = max(1, min(limit, JOURNAL_MAX_LIMIT))
    offset = max(0, offset)
    if platform not in platform_filter_values(metric):
        platform = "all"

    years = journal_years(db)
    if not years:
        return {
            "metric": metric,
            "year": year or datetime.now(timezone.utc).year,
            "years": [],
            "platform": platform,
            "offset": offset,
            "limit": limit,
            "total_rows": 0,
            "latest_event_date": None,
            "built_at": None,
            "rows": [],
            "me": None,
        }
    if year is None or year not in years:
        year = years[0]

    snapshot = get_leaderboard_snapshot(db, metric, platform=platform)  # type: ignore[arg-type]
    built_at = cast("str | None", snapshot.get("built_at"))

    cache_key = _journal_cache_key(metric, year, platform, offset, limit)
    payload: dict[str, object] | None = None
    if use_cache:
        cached = _read_raw_cache(cache_key)
        # Журнал обязан рассказывать то же, что таблица рейтинга рядом с ним:
        # пересчитанный снапшот обесценивает закэшированные страницы журнала.
        if cached is not None and cached.get("built_at") == built_at:
            payload = cached

    if payload is None:
        entity_keys = cast("list[str]", snapshot.get("entity_keys") or [])
        snapshot_rows = cast("list[dict[str, object]]", snapshot.get("rows") or [])
        page_keys = entity_keys[offset : offset + limit]
        page_rows = snapshot_rows[offset : offset + limit]
        rows = _build_journal_rows(db, metric, page_keys, page_rows, year, platform)
        payload = {
            "metric": metric,
            "year": year,
            "years": years,
            "platform": platform,
            "offset": offset,
            "limit": limit,
            "total_rows": len(entity_keys),
            "latest_event_date": snapshot.get("latest_event_date"),
            "built_at": built_at,
            "rows": rows,
        }
        if use_cache:
            _write_raw_cache(cache_key, payload)

    result = dict(payload)
    # Строка «Вы» зависит от зрителя — в кэш страницы она не попадает.
    result["me"] = (
        _viewer_journal_row(db, metric, viewer, snapshot, year, platform)
        if viewer is not None
        else None
    )
    return result
