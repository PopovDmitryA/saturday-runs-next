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

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.geo.country_names import normalize_country_name
from app.models import Location, Platform
from app.parkrun import key_plan
from app.parkrun.catalog_store import prefetch_catalog_events, resolve_catalog_event
from app.parkrun.event_slugs import COUNTRY_BY_SLUG
from app.parkrun.location_keys import merge_parkrun_locations, rename_parkrun_location, rewrite_event_keys

logger = logging.getLogger(__name__)

# Точка сайта считается «прежней точкой каталога», если до неё меньше 100 м.
SAME_POINT_KM = 0.1
# Nominatim — 1 запрос в секунду; за прогон геокодируем не больше стольких
# (первый прогон на проде — 33 точки; обычный день — единицы).
GEOCODE_LIMIT = 50
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
    # Ключи не по слагу parkrun, для которых каталог слага не знает (французские
    # площадки и т.п.) — в сводку не идут, видны в итоге прогона.
    unknown: list[str] = field(default_factory=list)
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
            "unknown": self.unknown,
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
        report.unknown = sorted(str(item.get("key") or item.get("external_key") or "") for item in plan["unknown"])
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

    planned: dict = {}  # холостой прогон: переименования откатаны, пробелы смотрим под новым слагом
    for location_id, old_key, old_name, slug in renames:
        def rename(db, location_id=location_id, slug=slug, old_name=old_name):
            location = db.query(Location).filter(Location.id == location_id).with_for_update().one()
            rename_parkrun_location(db, location, slug, new_name=view.new_name(slug, old_name))
            return {}

        outcome = key_plan.run_group(session_factory, apply, rename)
        if outcome.get("ok"):
            renamed_to = view.new_name(slug, old_name)
            label = f"{old_key} → {slug}"
            if renamed_to and renamed_to != old_name:
                label += f" (имя: {old_name} → {renamed_to})"
            report.renamed.append(label)
            if not apply:
                planned[location_id] = slug
            report.touched_location_ids.add(location_id)
            report.reshaped_location_ids.add(location_id)
        else:
            report.conflicts.append(f"переименование {old_key} → {slug}: {outcome.get('error')}")

    _fill_gaps(session_factory, platform_id, report, apply=apply, geocode=geocode, planned=planned)
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


def _fill_gaps(
    session_factory,
    platform_id,
    report: HealReport,
    *,
    apply: bool,
    geocode: bool,
    planned: dict | None = None,
) -> None:
    """Точка, ссылки и страна parkrun-локаций — тремя проходами.

    1. Точка и ссылки (локации и её протоколов) — каждая локация в своей точке
       сохранения: сбой одной (блокировка импортом профиля, конфликт ключа
       протокола) не откатывает остальные. В отчёт — только после commit.
    2. Страна по точке — Nominatim, без открытой транзакции
       (idle_in_transaction_session_timeout 60 с убил бы соединение посреди
       пачки запросов), каждая локация — своя короткая запись.
    3. Страна, которую не дал геокод: по таблице географии (COUNTRY_BY_SLUG),
       иначе страна площадки в каталоге.

    Город лечение не ставит: у сельских и военных площадок Nominatim отдаёт
    район («Uttlesford», «Lesedi Local Municipality») или «Большой Лондон», а
    рейтинг городов считает каждый такой новым городом (проверка 21.10.2026).

    `planned` — {id локации: новый слаг} у переименований, которые холостой
    прогон откатил: предпросмотр показывает и их пробелы, как сделает
    настоящий прогон после переименования.
    """
    planned = planned or {}
    geo_candidates: list[tuple] = []
    fallback: list[tuple] = []
    points: list[str] = []
    links = 0
    touched: set = set()
    db = session_factory()
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
                or_(
                    text("locations.external_key in (select slug from parkrun_events)"),
                    Location.id.in_(list(planned) or [None]),
                ),
            )
            .order_by(Location.external_key)
            .all()
        )
        stale_protocols = _locations_with_stale_protocol_links(db, platform_id)
        slug_of = {location.id: planned.get(location.id, location.external_key) for location in rows}
        prefetch_catalog_events(db, set(slug_of.values()))
        for location in rows:
            slug = slug_of[location.id]
            event = resolve_catalog_event(db, slug)
            if event is None:
                continue
            label = location.name or slug
            simulated = location.id in planned
            try:
                with db.begin_nested():
                    point_set = False
                    if (
                        location.latitude is None or location.longitude is None
                    ) and event.latitude is not None and event.country_code != 79:
                        if not simulated:
                            location.latitude, location.longitude = event.latitude, event.longitude
                        point_set = True
                    page_url = event.page_url
                    # Ссылки переименуемой локации ставит само переименование.
                    # Протоколы перепривязываем и тогда, когда ссылка локации уже
                    # верная, а протоколы остались на чужом домене (Флоренция:
                    # 31 протокол на parkrun.org.uk при ссылке на parkrun.it).
                    link_fixed = (
                        bool(page_url and location.source_url != page_url) or location.id in stale_protocols
                    ) and not simulated
                    if link_fixed:
                        if page_url:
                            location.source_url = page_url
                        db.flush()
                        rewrite_event_keys(db, location)
                    db.flush()
            except Exception as exc:  # noqa: BLE001 — одна локация не валит остальные
                logger.warning("parkrun catalog heal: %s не поправлена", slug, exc_info=True)
                report.conflicts.append(f"пробелы {slug}: {_error(exc)}")
                continue
            if point_set:
                points.append(label)
            if link_fixed:
                links += 1
            if point_set or link_fixed:
                touched.add(location.id)
            has_point = point_set or (location.latitude is not None and location.longitude is not None)
            if (location.country or "").strip():
                continue
            if has_point and slug not in COUNTRY_BY_SLUG:
                lat = event.latitude if point_set else float(location.latitude)
                lon = event.longitude if point_set else float(location.longitude)
                # Сначала только что поставленные точки, потом прежние без страны.
                geo_candidates.append((0 if point_set else 1, slug, location.id, label, lat, lon))
            fallback.append((location.id, slug, label, country_names.get(event.country_code)))
        if apply:
            db.commit()
        else:
            db.rollback()
    except Exception as exc:  # noqa: BLE001 — лечение не должно ронять сверку
        db.rollback()
        logger.exception("parkrun catalog heal: пробелы не заполнены")
        report.conflicts.append(f"пробелы: {_error(exc)}")
        return
    finally:
        db.close()
    report.points_set += points
    report.links_fixed += links
    report.touched_location_ids |= touched
    report.reshaped_location_ids |= touched

    if apply and geocode:
        for _, slug, location_id, label, lat, lon in sorted(geo_candidates)[:GEOCODE_LIMIT]:
            if not _geocode_location(session_factory, slug, location_id, label, lat, lon, report):
                break  # Nominatim не отвечает — не долбим его остальными; страну даст каталог

    _fill_countries(session_factory, fallback, report, apply=apply)


def _locations_with_stale_protocol_links(db: Session, platform_id) -> set:
    """Локации, у которых протоколы ссылаются не на домен страны площадки."""
    rows = db.execute(
        text(
            """
            select distinct l.id
              from locations l
              join parkrun_events pe on pe.slug = l.external_key
              join parkrun_countries c on c.code = pe.country_code and c.is_active and c.domain is not null
              join events e on e.location_id = l.id
             where l.platform_id = :platform
               and e.source_url is not null and e.source_url <> ''
               and e.source_url not like 'https://' || c.domain || '/' || l.external_key || '/results/%'
            """
        ),
        {"platform": platform_id},
    ).all()
    return {row[0] for row in rows}


def _geocode_location(session_factory, slug: str, location_id, label: str, lat: float, lon: float, report: HealReport) -> bool:
    """Страна по точке. Сеть — без открытой транзакции, запись — короткая.

    False — Nominatim не ответил (остальные локации этого прогона не геокодируем).
    """
    from celery.exceptions import SoftTimeLimitExceeded

    from app.geo import reverse_geocode

    try:
        address = reverse_geocode.lookup_address(lat, lon) or {}
    except SoftTimeLimitExceeded:
        raise
    except Exception:  # noqa: BLE001 — геокод не обязателен
        logger.warning("parkrun catalog heal: геокод %s не ответил", slug, exc_info=True)
        return False
    country = normalize_country_name(address.get("country"))
    if not country:
        return True
    db = session_factory()
    try:
        db.execute(text("set local lock_timeout = '5s'"))
        location = db.get(Location, location_id)
        if location is None or (location.country or "").strip():
            return True
        location.country = country
        db.commit()
        report.countries_set.append(f"{label}: {country} (по точке)")
        report.reshaped_location_ids.add(location_id)
        report.touched_location_ids.add(location_id)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        report.conflicts.append(f"страна {slug}: {_error(exc)}")
    finally:
        db.close()
    return True


def _fill_countries(session_factory, fallback: list[tuple], report: HealReport, *, apply: bool) -> None:
    """Страна, которую не дал геокод: таблица географии, иначе страна каталога."""
    if not fallback:
        return
    db = session_factory()
    try:
        db.execute(text("set local lock_timeout = '5s'"))
        written: list[tuple] = []
        for location_id, slug, label, catalog_country in fallback:
            country = COUNTRY_BY_SLUG.get(slug) or catalog_country
            if not country:
                continue
            location = db.get(Location, location_id)
            if location is None or (location.country or "").strip():
                continue  # страну уже поставил геокод
            try:
                with db.begin_nested():
                    location.country = country
                    db.flush()
            except Exception as exc:  # noqa: BLE001
                report.conflicts.append(f"страна {slug}: {_error(exc)}")
                continue
            written.append((location_id, label, country))
        if apply:
            db.commit()
        else:
            db.rollback()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        report.conflicts.append(f"страны: {_error(exc)}")
        return
    finally:
        db.close()
    for location_id, label, country in written:
        report.countries_set.append(f"{label}: {country}")
        report.touched_location_ids.add(location_id)
        report.reshaped_location_ids.add(location_id)


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
