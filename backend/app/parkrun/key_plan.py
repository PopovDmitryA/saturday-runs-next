"""План приведения ключей parkrun-локаций к слагам parkrun: кого слить, кого переименовать.

Общий для разового `scripts/unify_parkrun_location_keys.py` (16.10.2026, по
снимку каталога в репо) и ежедневного лечения после сверки с каталогом
(services/parkrun_catalog_heal.py, по каталогу в базе). Источник слагов —
`CatalogView`: что считается официальным слагом, какой слаг у названия
площадки, где её точка старта и какое у неё сейчас имя.

Порядок поиска слага для локации: ключ уже официальный → официальный слаг с тем
же ключом без разделителей → слаг по названию площадки, но только если точка
старта рядом (до километра) или точки у локации нет вовсе — так находятся
переименованные площадки и не находятся чужие тёзки. Группа локаций одного
слага: основная — с официальным ключом (иначе связанная с каталогом локаций,
иначе старшая), остальные сливаются в неё.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Location, LocationCatalogLink, Platform
from app.parkrun.location_keys import ParkrunKeyConflict

SUMMARY_KEY = "parkrun-summary"
NAME_MATCH_MAX_KM = 1.0


def norm(value: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", "", (value or "").lower())


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


@dataclass
class CatalogView:
    official: set[str]
    slug_by_name: Callable[[str], str | None]
    point: Callable[[str], tuple[float, float] | None]
    # Новое имя локации при переименовании (parkrun сменил имя вместе со слагом).
    new_name: Callable[[str, str | None], str | None] = lambda slug, old: None
    by_norm: dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        grouped: dict[str, set[str]] = defaultdict(set)
        for slug in self.official:
            grouped[norm(slug)].add(slug)
        self.by_norm = dict(grouped)


def official_slug_for(location: Location, view: CatalogView) -> tuple[str | None, str]:
    key = location.external_key
    if key in view.official:
        return key, "key"
    candidates = view.by_norm.get(norm(key)) or set()
    if len(candidates) == 1:
        return next(iter(candidates)), "key-without-separators"
    by_name = view.slug_by_name(location.name or "")
    if by_name:
        point = view.point(by_name)
        if location.latitude is None or location.longitude is None:
            # Точки нет — сверить не с чем; имя в каталоге однозначно (иначе
            # slug_by_name вернул бы None), этого достаточно (Okura kaigan koen).
            return (by_name, "name") if point is not None else (None, f"name-only:{by_name}")
        if point is not None and distance_km(float(location.latitude), float(location.longitude), *point) <= NAME_MATCH_MAX_KM:
            return by_name, "name+point"
        return None, f"name-only:{by_name}"
    return None, "unknown"


def build_plan(db: Session, platform: Platform, view: CatalogView) -> dict:
    locations = (
        db.query(Location)
        .filter(Location.platform_id == platform.id, Location.external_key != SUMMARY_KEY)
        .order_by(Location.created_at)
        .all()
    )
    linked = {
        row[0]
        for row in db.query(LocationCatalogLink.location_id).filter(LocationCatalogLink.location_id.isnot(None)).all()
    }
    groups: dict[str, list[Location]] = defaultdict(list)
    unknown: list[dict] = []
    name_only: list[dict] = []
    for location in locations:
        slug, how = official_slug_for(location, view)
        if slug is None:
            entry = {"key": location.external_key, "name": location.name}
            (name_only if how.startswith("name-only") else unknown).append({**entry, "why": how})
            continue
        groups[slug].append(location)

    merges: list[tuple[Location, Location, str]] = []
    renames: list[tuple[Location, str]] = []
    for slug, members in groups.items():
        if len(members) == 1:
            if members[0].external_key != slug:
                renames.append((members[0], slug))
            continue
        keep = next((m for m in members if m.external_key == slug), None)
        if keep is None:
            keep = next((m for m in members if m.id in linked), members[0])
        for member in members:
            if member.id != keep.id:
                merges.append((keep, member, slug))
        if keep.external_key != slug:
            renames.append((keep, slug))
    return {"locations": locations, "merges": merges, "renames": renames, "unknown": unknown, "name_only": name_only}


def run_group(session_factory, apply: bool, action) -> dict:
    """Одна группа — одна транзакция; при холостом прогоне она откатывается."""
    db = session_factory()
    try:
        db.execute(text("set local lock_timeout = '5s'"))
        db.execute(text("set local statement_timeout = '120s'"))
        result = action(db)
        if apply:
            db.commit()
        else:
            db.rollback()
        return {"ok": True, **(result or {})}
    except (ParkrunKeyConflict, SQLAlchemyError) as exc:
        db.rollback()
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"}
    finally:
        db.close()


def finish_location_changes(
    session_factory,
    *,
    participants: set,
    touched: set,
    reshaped: set,
    reason: str,
    repair_first_runs: bool = True,
) -> dict:
    """После слияний и переименований: флаги первого старта, рекорды, кэши витрин.

    Общая для разового скрипта и ежедневного лечения. Флаги «первый старт»
    чинятся от состояния базы (repair_first_run_flags сам находит расхождения),
    поэтому повторный прогон доделает их и после обрыва. Рекорды локации — у
    бегавших на двойнике (participants), кабинеты — у людей с пробежками на
    локациях, сменивших слаг или страну (reshaped): кабинет хранит их у каждой
    пробежки. Каждый шаг сам по себе: сбой одного не отменяет остальные.
    """
    from app.services.dashboard_service import invalidate_dashboard_cache_for_users
    from app.services.location_freshness import mark_location_results_changed
    from app.services.location_page_service import invalidate_locations_index_cache
    from app.services.personal_record_service import (
        recalculate_participants_cross_platform_personal_records,
        repair_first_run_flags,
    )

    report: dict = {}
    failures: list[str] = []

    def step(name: str, action) -> None:
        db = session_factory()
        try:
            result = action(db)
            db.commit()
            if result is not None:
                report[name] = result
        except Exception as exc:  # noqa: BLE001 — остальные шаги важнее
            db.rollback()
            failures.append(f"{name}: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
        finally:
            db.close()

    if repair_first_runs:
        step("first_run_repair", lambda db: repair_first_run_flags(db, "parkrun"))

    # Рекорд локации считается по физической локации у человека в целом —
    # у бегавших на двойнике он мог поменяться.
    ids = sorted({p for p in participants if p is not None}, key=str)
    for start in range(0, len(ids), 200):
        batch = ids[start : start + 200]
        step("records_batch", lambda db, batch=batch: recalculate_participants_cross_platform_personal_records(db, batch))
    report["records_participants"] = len(ids)

    def reset_dashboards(db):
        if not reshaped:
            return 0
        users = {
            row[0]
            for row in db.execute(
                text(
                    """
                    select distinct pl.user_id
                      from platform_links pl
                      join run_results rr on rr.participant_id = pl.participant_id
                      join events e on e.id = rr.event_id
                     where e.location_id = any(cast(:ids as uuid[])) and pl.user_id is not null
                    """
                ),
                {"ids": [str(i) for i in reshaped]},
            ).all()
        }
        return invalidate_dashboard_cache_for_users(db, users)

    step("dashboards_reset", reset_dashboards)
    if touched:
        step("caches", lambda db: mark_location_results_changed(db, touched, reason=reason))
    invalidate_locations_index_cache()
    if failures:
        report["finish_failed"] = failures
    return report


def snapshot_view(
    catalog: Callable[[], dict[str, str]],
    slug_by_name: Callable[[str], str | None],
    catalog_event: Callable[[str], object],
    renamed_names: dict[str, str],
) -> CatalogView:
    def point(slug: str) -> tuple[float, float] | None:
        event = catalog_event(slug)
        return (event.latitude, event.longitude) if event is not None else None  # type: ignore[attr-defined]

    return CatalogView(
        official=set(catalog().values()),
        slug_by_name=slug_by_name,
        point=point,
        new_name=lambda slug, old: renamed_names.get(slug),
    )


def database_view(db: Session) -> CatalogView | None:
    """Вид каталога из базы. Каталог не засеян — None (работать по снимку)."""
    try:
        with db.begin_nested():
            rows = db.execute(text("select slug, latitude, longitude, short_name from parkrun_events")).all()
            names = db.execute(
                text(
                    """
                    select n.name_norm, e.slug, n.kind, e.is_active
                      from parkrun_event_names n join parkrun_events e on e.id = n.event_id
                    """
                )
            ).all()
    except SQLAlchemyError:
        return None
    if not rows:
        return None
    points = {slug: (lat, lon) for slug, lat, lon, _ in rows if lat is not None and lon is not None}
    short_names = {slug: short for slug, _, _, short in rows}
    by_name: dict[str, set[str]] = defaultdict(set)
    active_by_name: dict[str, set[str]] = defaultdict(set)
    from app.parkrun.event_slugs import normalize_event_name

    current_short = {slug: normalize_event_name(short or "") for slug, short in short_names.items()}
    old_names: set[tuple[str, str]] = set()
    for name_norm, slug, kind, is_active in names:
        by_name[name_norm].add(slug)
        if is_active:
            active_by_name[name_norm].add(slug)
        # Старое имя — помеченное так сверкой или короткое имя, которое уже не
        # совпадает с нынешним (строки до 21.10.2026 переименование не помечало).
        if kind == "old" or (kind == "short" and name_norm != current_short.get(slug)):
            old_names.add((name_norm, slug))

    def slug_by_name(name: str) -> str | None:
        key = normalize_event_name(name or "")
        candidates = by_name.get(key) or set()
        if len(candidates) == 1:
            return next(iter(candidates))
        active = active_by_name.get(key) or set()
        return next(iter(active)) if len(active) == 1 else None

    def new_name(slug: str, old: str | None) -> str | None:
        # Имя меняем, только если у локации старое имя площадки (её переименовал parkrun).
        if old and (normalize_event_name(old), slug) in old_names:
            return short_names.get(slug)
        return None

    return CatalogView(official=set(short_names), slug_by_name=slug_by_name, point=lambda slug: points.get(slug), new_name=new_name)
