"""Лечение parkrun-локаций сайта по каталогу в базе — после каждой сверки.

Что сайт делает сам (решение Дмитрия 18.10.2026: «сайт сам держит
parkrun-локации в порядке»):
* ключ на транслитерации или старом слаге → слаг parkrun; если локация с
  этим слагом уже есть — сливаем двойника (app/parkrun/key_plan.py,
  app/parkrun/location_keys.py);
* нет точки старта — ставим из каталога (и город обратным геокодом);
  нет страны — из страны каталога;
* ссылка на площадку не на домене её страны — на домен страны, вместе со
  ссылками протоколов;
* parkrun перенёс старт — переносим точку, только если на сайте стоит
  прежняя точка каталога (иначе её правили руками — говорим, но не трогаем).

Не трогаем: Россию и Францию (их площадки закрыты, у страны нет домена),
страну, если она уже есть (код страны у parkrun — подчинение, а не география:
Намибия числится за ЮАР), и всё, для чего слаг parkrun неизвестен.
Каждая группа — своя короткая транзакция, как у разового скрипта.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.geo.country_names import normalize_country_name
from app.models import Location, Platform
from app.parkrun import key_plan
from app.parkrun.catalog_store import prefetch_catalog_events, resolve_catalog_event
from app.parkrun.location_keys import merge_parkrun_locations, rename_parkrun_location, rewrite_event_keys

logger = logging.getLogger(__name__)

# Точка сайта считается «прежней точкой каталога», если до неё меньше 100 м.
SAME_POINT_KM = 0.1
# Nominatim — 1 запрос в секунду; за прогон геокодируем не больше стольких.
GEOCODE_LIMIT = 20
# Предохранитель от испорченного каталога: план больше этого — ничего не
# сливаем и не переименовываем, а просим посмотреть руками
# (scripts/parkrun_catalog_run.py --heal-preview, затем --heal-no-limit).
# Обычный день — единицы; первый прогон на проде (21.10.2026) — 87 переименований.
MAX_MERGES = 20
MAX_RENAMES = 120


@dataclass
class HealReport:
    merged: list[str] = field(default_factory=list)
    renamed: list[str] = field(default_factory=list)
    points_set: list[str] = field(default_factory=list)
    countries_set: list[str] = field(default_factory=list)
    links_fixed: int = 0
    moved: list[dict[str, Any]] = field(default_factory=list)
    moved_skipped: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    touched_location_ids: set = field(default_factory=set)
    # Для доводки после записи (key_plan.finish_location_changes), в сводку не идут.
    reshaped_location_ids: set = field(default_factory=set)
    merged_participants: set = field(default_factory=set)
    finish: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "merged": self.merged,
            "renamed": self.renamed,
            "points_set": self.points_set,
            "countries_set": self.countries_set,
            "links_fixed": self.links_fixed,
            "moved": self.moved,
            "moved_skipped": self.moved_skipped,
            "conflicts": self.conflicts,
            **({"finish_failed": self.finish["finish_failed"]} if self.finish.get("finish_failed") else {}),
        }

    @property
    def anything(self) -> bool:
        return bool(
            self.merged or self.renamed or self.points_set or self.countries_set or self.links_fixed
            or self.moved or self.moved_skipped or self.conflicts
        )


def heal_parkrun_locations(
    session_factory,
    *,
    moved_events: list[dict[str, Any]] | None = None,
    apply: bool = True,
    geocode: bool = True,
    limits: bool = True,
) -> HealReport:
    """Полечить parkrun-локации сайта. Сбой любого шага — строка в conflicts, не исключение."""
    report = HealReport()
    db = session_factory()
    try:
        platform = db.query(Platform).filter(Platform.code == "parkrun").one_or_none()
        if platform is None:
            report.conflicts.append("нет платформы parkrun — лечение пропущено")
            return report
        view = key_plan.database_view(db)
        if view is None:
            report.conflicts.append("каталог в базе не засеян — лечение пропущено")
            return report
        plan = key_plan.build_plan(db, platform, view)
        merges = [(keep.id, keep.external_key, drop.id, drop.external_key) for keep, drop, _ in plan["merges"]]
        renames = [(loc.id, loc.external_key, loc.name, slug) for loc, slug in plan["renames"]]
        platform_id = platform.id
    except Exception as exc:  # noqa: BLE001 — лечение не должно ронять сверку
        logger.exception("parkrun catalog heal: план не собран")
        report.conflicts.append(f"план: {_error(exc)}")
        return report
    finally:
        db.close()

    if limits and (len(merges) > MAX_MERGES or len(renames) > MAX_RENAMES):
        report.conflicts.append(
            f"план больше обычного ({len(merges)} слияний, {len(renames)} переименований; порог "
            f"{MAX_MERGES}/{MAX_RENAMES}) — ничего не слито и не переименовано. Проверить: "
            "scripts/parkrun_catalog_run.py --heal-preview, применить: --heal-no-limit"
        )
        merges, renames = [], []

    for keep_id, keep_key, drop_id, drop_key in merges:
        def merge(db, keep_id=keep_id, drop_id=drop_id):
            keep = db.query(Location).filter(Location.id == keep_id).with_for_update().one()
            drop = db.query(Location).filter(Location.id == drop_id).with_for_update().one_or_none()
            if drop is None:
                return {}
            return {"participants": merge_parkrun_locations(db, keep, drop).participants}

        outcome = key_plan.run_group(session_factory, apply, merge)
        if outcome.get("ok"):
            report.merged.append(f"{drop_key} → {keep_key}")
            report.touched_location_ids.add(keep_id)
            report.reshaped_location_ids.add(keep_id)
            report.merged_participants |= set(outcome.get("participants") or ())
        else:
            report.conflicts.append(f"слияние {drop_key} → {keep_key}: {outcome.get('error')}")

    for location_id, old_key, old_name, slug in renames:
        def rename(db, location_id=location_id, slug=slug, old_name=old_name):
            location = db.query(Location).filter(Location.id == location_id).with_for_update().one()
            rename_parkrun_location(db, location, slug, new_name=view.new_name(slug, old_name))
            return {}

        outcome = key_plan.run_group(session_factory, apply, rename)
        if outcome.get("ok"):
            report.renamed.append(f"{old_key} → {slug}")
            report.touched_location_ids.add(location_id)
            report.reshaped_location_ids.add(location_id)
        else:
            report.conflicts.append(f"переименование {old_key} → {slug}: {outcome.get('error')}")

    _fill_gaps(session_factory, platform_id, report, apply=apply, geocode=geocode)
    _follow_moves(session_factory, platform_id, moved_events or [], report, apply=apply)

    if apply and (report.merged or report.reshaped_location_ids):
        report.finish = key_plan.finish_location_changes(
            session_factory,
            participants=report.merged_participants,
            touched=set(),  # кэши локаций сбрасывает сверка — по touched_location_ids
            reshaped=report.reshaped_location_ids,
            reason="parkrun: сверка с каталогом",
            repair_first_runs=bool(report.merged),
        )
    return report


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:200]}"


def _fill_gaps(session_factory, platform_id, report: HealReport, *, apply: bool, geocode: bool) -> None:
    db = session_factory()
    geocoded = 0
    try:
        db.execute(text("set local lock_timeout = '5s'"))
        country_names = {
            code: normalize_country_name(name)
            for code, name in db.execute(text("select code, name from parkrun_countries")).all()
        }
        rows = (
            db.query(Location)
            .filter(
                Location.platform_id == platform_id,
                text("locations.external_key in (select slug from parkrun_events)"),
            )
            .all()
        )
        prefetch_catalog_events(db, [location.external_key for location in rows])
        for location in rows:
            event = resolve_catalog_event(db, location.external_key)
            if event is None:
                continue
            changed = False
            if (location.latitude is None or location.longitude is None) and event.country_code != 79:
                location.latitude = event.latitude
                location.longitude = event.longitude
                report.points_set.append(location.name or location.external_key)
                changed = True
                if geocode and geocoded < GEOCODE_LIMIT and (location.city is None or location.country is None):
                    geocoded += 1
                    _geocode_city(location, event)
            if not (location.country or "").strip():
                country = country_names.get(event.country_code)
                if country:
                    location.country = country
                    report.countries_set.append(f"{location.name or location.external_key}: {country}")
                    changed = True
            page_url = event.page_url
            if page_url and location.source_url != page_url:
                location.source_url = page_url
                db.flush()
                rewrite_event_keys(db, location)
                report.links_fixed += 1
                changed = True
            if changed:
                report.touched_location_ids.add(location.id)
                report.reshaped_location_ids.add(location.id)
        if apply:
            db.commit()
        else:
            db.rollback()
    except Exception as exc:  # noqa: BLE001 — лечение не должно ронять сверку
        db.rollback()
        logger.exception("parkrun catalog heal: пробелы не заполнены")
        report.conflicts.append(f"пробелы: {_error(exc)}")
    finally:
        db.close()


def _geocode_city(location: Location, event) -> None:
    from app.platform_adapters.canonical import CanonicalLocation
    from app.sync.upsert import _resolve_geo

    country, _region, city = _resolve_geo(
        CanonicalLocation(
            external_key=location.external_key,
            name=location.name,
            latitude=event.latitude,
            longitude=event.longitude,
        ),
        location,
    )
    location.country = location.country or country
    location.city = location.city or city


def _follow_moves(session_factory, platform_id, moved_events: list[dict[str, Any]], report: HealReport, *, apply: bool) -> None:
    if not moved_events:
        return
    db = session_factory()
    try:
        db.execute(text("set local lock_timeout = '5s'"))
        for item in moved_events:
            location = (
                db.query(Location)
                .filter(Location.platform_id == platform_id, Location.external_key == item["slug"])
                .one_or_none()
            )
            if location is None or location.latitude is None or location.longitude is None:
                continue
            old_lat, old_lon = item["old"]
            new_lat, new_lon = item["new"]
            entry = {"slug": item["slug"], "name": location.name, "distance_km": item.get("distance_km")}
            site_to_old = key_plan.distance_km(float(location.latitude), float(location.longitude), old_lat, old_lon)
            site_to_new = key_plan.distance_km(float(location.latitude), float(location.longitude), new_lat, new_lon)
            if site_to_new <= SAME_POINT_KM:
                continue
            if site_to_old <= SAME_POINT_KM:
                location.latitude, location.longitude = new_lat, new_lon
                report.moved.append(entry)
                report.touched_location_ids.add(location.id)
            else:
                report.moved_skipped.append({**entry, "site_to_new_km": round(site_to_new, 2)})
        if apply:
            db.commit()
        else:
            db.rollback()
    except Exception as exc:  # noqa: BLE001 — лечение не должно ронять сверку
        db.rollback()
        logger.exception("parkrun catalog heal: переносы старта не применены")
        report.moved, report.moved_skipped = [], []
        report.conflicts.append(f"переносы старта: {_error(exc)}")
    finally:
        db.close()
