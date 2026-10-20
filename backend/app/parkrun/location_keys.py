"""Ключи parkrun-локаций: один слаг на площадку — в локации, протоколах и результатах.

parkrun пишет слаг строчными латинскими буквами и цифрами без разделителей,
дефис бывает только в окончании `-juniors` (каталог events.json, 3117 площадок).
У нас слаг живёт в четырёх местах сразу: `locations.external_key`, связки
каталога локаций, ключ протокола `events.external_event_key` (`дата:слаг`) со
ссылкой `events.source_url` и ключ результата `run_results.external_result_key`
(`parkrun:<атлет|unknown>:<слаг>:<дата>:<номер|место>`).

Раньше переименование трогало только локацию и связку (`parkrun_catalog_sync.py
--rename-keys`), и к 16.10.2026 у 58 тыс. протоколов ключ и ссылка остались на
старом слаге (`bushy-park` у локации `bushy`, у Łódź вообще `d`). А транслитерация
российских названий (`gorky-park`) при официальном `gorkypark` завела 55 пар
локаций-двойников, между которыми импорт профиля перетаскивал протоколы.

Здесь — два действия, которыми пользуются и сверка с каталогом, и разовое
приведение ключей (`scripts/unify_parkrun_location_keys.py`):

* `rename_parkrun_location` — новый ключ локации и всё, что от него строится;
* `merge_parkrun_locations` — перенести всё с двойника на основную локацию и
  удалить двойника.

Обе функции не коммитят: транзакцией управляет вызывающий.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Event, Location, LocationCatalog, LocationCatalogLink
from app.parkrun.catalog_store import resolve_catalog_event
from app.services.location_catalog_service import CATALOG_KEY_REFERENCES

logger = logging.getLogger(__name__)

FALLBACK_DOMAIN = "www.parkrun.org.uk"
PLACEHOLDER_PREFIX = "parkrun:unknown:"


class ParkrunKeyConflict(RuntimeError):
    """Слияние или переименование нельзя провести автоматически — нужен человек."""


def location_page_url(slug: str, db: Session | None = None) -> str | None:
    """Ссылка на площадку на домене её страны (для России и Франции — нет)."""
    event = resolve_catalog_event(db, slug)
    return event.page_url if event is not None else None


def event_results_url_sql_prefix(slug: str, db: Session | None = None) -> str:
    """Начало ссылки на протокол: `https://<домен>/<слаг>/results/`.

    Домен — страны площадки по каталогу; у ушедших из parkrun стран (Россия,
    Франция) и у площадок вне каталога — www.parkrun.org.uk, как пишет и импорт
    профиля (upsert.import_profile_run_results).
    """
    event = resolve_catalog_event(db, slug)
    domain = event.domain if event is not None and event.domain else FALLBACK_DOMAIN
    return f"https://{domain}/{slug}/results/"


@dataclass
class KeyRewrite:
    events: int = 0
    results: int = 0


def result_key_with_slug(key: str | None, slug: str) -> str | None:
    """Ключ результата parkrun с другим слагом площадки — как rewrite_event_keys.

    `parkrun:<атлет|unknown>:<слаг>:<дата>:<номер|место>` — меняется только
    третья часть; ключ другого вида возвращается как есть.
    """
    if not key or not key.startswith("parkrun:"):
        return key
    parts = key.split(":")
    if len(parts) != 5 or parts[2] == slug:
        return key
    parts[2] = slug
    return ":".join(parts)


def rewrite_event_keys(db: Session, location: Location) -> KeyRewrite:
    """Ключи и ссылки протоколов и ключи результатов — под текущий ключ локации.

    Строки только обновляются, а не пересоздаются: сканер уведомлений ищет
    новые результаты по created_at и разослал бы людям «новые пробежки».
    """
    slug = location.external_key
    prefix = event_results_url_sql_prefix(slug, db)
    events = db.execute(
        text(
            """
            update events
               set external_event_key = to_char(event_date, 'YYYY-MM-DD') || ':' || :slug,
                   source_url = :prefix || coalesce(event_number::text, '') || '/'
             where location_id = :location_id
               and (external_event_key is distinct from to_char(event_date, 'YYYY-MM-DD') || ':' || :slug
                    or source_url is distinct from :prefix || coalesce(event_number::text, '') || '/')
            """
        ),
        {"slug": slug, "prefix": prefix, "location_id": location.id},
    ).rowcount
    # У ключа результата меняется ровно третья часть. Четвёртая и пятая — дата
    # и номер забега (у заглушки «неизвестного» — место), их не трогаем.
    results = db.execute(
        text(
            """
            update run_results rr
               set external_result_key = split_part(rr.external_result_key, ':', 1) || ':'
                   || split_part(rr.external_result_key, ':', 2) || ':' || :slug || ':'
                   || split_part(rr.external_result_key, ':', 4) || ':'
                   || split_part(rr.external_result_key, ':', 5)
              from events e
             where rr.event_id = e.id
               and e.location_id = :location_id
               and rr.external_result_key like 'parkrun:%'
               and array_length(string_to_array(rr.external_result_key, ':'), 1) = 5
               and split_part(rr.external_result_key, ':', 3) <> :slug
            """
        ),
        {"slug": slug, "location_id": location.id},
    ).rowcount
    return KeyRewrite(events=events or 0, results=results or 0)


def rename_parkrun_location(
    db: Session,
    location: Location,
    new_key: str,
    *,
    new_name: str | None = None,
) -> KeyRewrite:
    """Новый ключ parkrun-локации — и в связках, и в протоколах, и в результатах."""
    old_key = location.external_key
    if old_key == new_key:
        return rewrite_event_keys(db, location)
    taken = (
        db.query(Location.id)
        .filter(
            Location.platform_id == location.platform_id,
            Location.external_key == new_key,
            Location.id != location.id,
        )
        .first()
    )
    if taken is not None:
        raise ParkrunKeyConflict(f"ключ {new_key} уже занят другой локацией — нужно слияние, а не переименование")

    location.external_key = new_key
    if new_name:
        location.name = new_name
    page_url = location_page_url(new_key, db)
    if page_url:
        location.source_url = page_url
    elif location.source_url and f"/{old_key}/" in location.source_url:
        location.source_url = location.source_url.replace(f"/{old_key}/", f"/{new_key}/")
    db.flush()

    db.query(LocationCatalogLink).filter(
        LocationCatalogLink.platform_id == location.platform_id,
        LocationCatalogLink.external_key == old_key,
    ).update({"external_key": new_key}, synchronize_session=False)
    db.query(LocationCatalog).filter(LocationCatalog.legacy_parkrun_slug == old_key).update(
        {"legacy_parkrun_slug": new_key}, synchronize_session=False
    )
    db.execute(
        text("update runpark_location_mappings set legacy_parkrun_slug = :new where legacy_parkrun_slug = :old"),
        {"new": new_key, "old": old_key},
    )
    return rewrite_event_keys(db, location)


@dataclass
class MergeReport:
    events_moved: int = 0
    events_merged: int = 0
    results_moved: int = 0
    results_dropped: int = 0
    weather_moved: int = 0
    weather_dropped: int = 0
    weather_replaced: int = 0
    links_moved: int = 0
    links_dropped: int = 0
    other_rows: dict[str, int] = field(default_factory=dict)
    participants: set[UUID] = field(default_factory=set)
    rewrite: KeyRewrite = field(default_factory=KeyRewrite)


# Таблицы со ссылкой на locations, которые слияние разбирает само. Остальные
# находятся по схеме: если у двойника в них есть строки, их переносят простым
# UPDATE, а при конфликте уникальности слияние останавливается — пусть решит
# человек, а не молчаливое удаление.
_HANDLED_LOCATION_REFS = {"events", "start_weather", "location_catalog_links"}


def _location_references(db: Session) -> list[tuple[str, str]]:
    rows = db.execute(
        text(
            """
            select cl.relname, a.attname
              from pg_constraint c
              join pg_class cl on cl.oid = c.conrelid
              join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
             where c.contype = 'f' and c.confrelid = 'locations'::regclass
             order by 1, 2
            """
        )
    ).all()
    return [(table, column) for table, column in rows]


def _event_references(db: Session) -> list[tuple[str, str]]:
    rows = db.execute(
        text(
            """
            select cl.relname, a.attname
              from pg_constraint c
              join pg_class cl on cl.oid = c.conrelid
              join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
             where c.contype = 'f' and c.confrelid = 'events'::regclass
             order by 1, 2
            """
        )
    ).all()
    return [(table, column) for table, column in rows]


def _merge_same_day_event(db: Session, keep: Event, drop: Event, report: MergeReport) -> None:
    """Два протокола одной даты: результаты двойника переходят к основному.

    Участник, который уже есть в основном протоколе, — дубль (так было у всех
    пяти результатов green-point), его строку удаляем. Заглушку «неизвестного»
    на месте, которое в основном протоколе занято, тоже.
    """
    for table, column in _event_references(db):
        if table in ("run_results", "volunteer_results", "run_tracks"):
            continue
        count = db.execute(
            text(f'select count(*) from "{table}" where "{column}" = :event_id'), {"event_id": drop.id}
        ).scalar_one()
        if count:
            raise ParkrunKeyConflict(f"у протокола {drop.external_event_key} есть строки в {table} — сливать руками")

    dropped = db.execute(
        text(
            """
            delete from run_results d
             where d.event_id = :drop
               and (
                    (d.external_result_key not like :placeholder
                     and exists (select 1 from run_results k
                                  where k.event_id = :keep and k.participant_id = d.participant_id))
                 or (d.external_result_key like :placeholder
                     and exists (select 1 from run_results k
                                  where k.event_id = :keep and k.position = d.position))
               )
            returning d.participant_id
            """
        ),
        {"drop": drop.id, "keep": keep.id, "placeholder": PLACEHOLDER_PREFIX + "%"},
    ).all()
    report.results_dropped += len(dropped)
    moved = db.execute(
        text("update run_results set event_id = :keep where event_id = :drop returning participant_id"),
        {"drop": drop.id, "keep": keep.id},
    ).all()
    report.results_moved += len(moved)
    volunteers = db.execute(
        text(
            """
            delete from volunteer_results d
             where d.event_id = :drop
               and exists (select 1 from volunteer_results k
                            where k.event_id = :keep and k.participant_id = d.participant_id)
            """
        ),
        {"drop": drop.id, "keep": keep.id},
    ).rowcount
    report.results_dropped += volunteers or 0
    db.execute(text("update volunteer_results set event_id = :keep where event_id = :drop"), {"drop": drop.id, "keep": keep.id})
    db.execute(text("update run_tracks set event_id = :keep where event_id = :drop"), {"drop": drop.id, "keep": keep.id})
    db.execute(text("delete from events where id = :drop"), {"drop": drop.id})
    report.events_merged += 1

    from app.services.gender_position_service import recalculate_event_gender_positions

    recalculate_event_gender_positions(db, keep.id, "parkrun")


def merge_parkrun_locations(db: Session, keep: Location, drop: Location) -> MergeReport:
    """Всё с двойника `drop` — на основную `keep`, двойника удалить.

    Порядок важен: часть ссылок на locations стоит с ON DELETE CASCADE (погода,
    описания, профили трасс), поэтому сначала переносим строки и только потом
    удаляем двойника — иначе база молча снесла бы их вместе с ним.
    """
    if keep.platform_id != drop.platform_id or keep.id == drop.id:
        raise ParkrunKeyConflict("сливать можно только две разные локации одной системы")
    report = MergeReport()
    params = {"keep": keep.id, "drop": drop.id}
    # Флаги «первый старт на локации» меняются только у тех, кто бегал на
    # двойнике: у остальных их пробежки на основной и так все при ней.
    report.participants.update(
        row[0]
        for row in db.execute(
            text(
                "select distinct rr.participant_id from run_results rr join events e on e.id = rr.event_id "
                "where e.location_id = :drop and rr.participant_id is not null"
            ),
            params,
        ).all()
    )

    # Протоколы. Дата, на которую протокол есть у обеих, — слияние протоколов;
    # остальные переезжают целиком (уникальность — локация + дата).
    keep_by_date = {
        row.event_date: row for row in db.query(Event).filter(Event.location_id == keep.id).all()
    }
    for event in db.query(Event).filter(Event.location_id == drop.id).order_by(Event.event_date).all():
        twin = keep_by_date.get(event.event_date)
        if twin is not None:
            _merge_same_day_event(db, twin, event, report)
    report.events_moved = (
        db.execute(text("update events set location_id = :keep where location_id = :drop"), params).rowcount or 0
    )

    # Погода: одна строка на локацию и дату. Окончательная (era5) важнее
    # предварительной (archive), остальное у основной локации уже есть.
    report.weather_replaced = (
        db.execute(
            text(
                """
                delete from start_weather k
                 using start_weather d
                 where k.location_id = :keep and d.location_id = :drop and k.obs_date = d.obs_date
                   and d.source = 'era5' and k.source <> 'era5'
                """
            ),
            params,
        ).rowcount
        or 0
    )
    report.weather_dropped = (
        db.execute(
            text(
                """
                delete from start_weather d
                 using start_weather k
                 where d.location_id = :drop and k.location_id = :keep and k.obs_date = d.obs_date
                """
            ),
            params,
        ).rowcount
        or 0
    )
    report.weather_moved = (
        db.execute(text("update start_weather set location_id = :keep where location_id = :drop"), params).rowcount
        or 0
    )

    # Связки каталога: узел у основной локации уже есть — связку двойника
    # убираем (так у Раменского); иначе связка переезжает с новым ключом.
    for link in db.query(LocationCatalogLink).filter(LocationCatalogLink.location_id == drop.id).all():
        keep_linked = (
            db.query(LocationCatalogLink.id)
            .filter(LocationCatalogLink.location_id == keep.id, LocationCatalogLink.catalog_id == link.catalog_id)
            .first()
        )
        if keep_linked is not None:
            db.delete(link)
            report.links_dropped += 1
        else:
            link.location_id = keep.id
            link.external_key = keep.external_key
            report.links_moved += 1
    db.flush()

    for table, column in _location_references(db):
        if table in _HANDLED_LOCATION_REFS:
            continue
        count = db.execute(
            text(f'select count(*) from "{table}" where "{column}" = :drop'), params
        ).scalar_one()
        if not count:
            continue
        try:
            with db.begin_nested():
                db.execute(text(f'update "{table}" set "{column}" = :keep where "{column}" = :drop'), params)
        except IntegrityError as exc:
            raise ParkrunKeyConflict(f"{table}.{column}: {count} строк двойника конфликтуют с основной локацией") from exc
        report.other_rows[f"{table}.{column}"] = count

    # Ключи «личности» вида `location:<uuid>` (дом, оценки, гео-пинги) — на
    # основную. У связанных с каталогом ключ `catalog:<id>`, его слияние не меняет.
    for table, column in CATALOG_KEY_REFERENCES:
        db.execute(
            text(f'update "{table}" set "{column}" = :keep_key where "{column}" = :drop_key'),
            {"keep_key": f"location:{keep.id}", "drop_key": f"location:{drop.id}"},
        )

    # Часовой пояс и прочие поля — только пустые у основной.
    for attr in ("timezone", "tz_offset_moscow", "latitude", "longitude", "country", "city", "region", "map_url"):
        if getattr(keep, attr) is None and getattr(drop, attr) is not None:
            setattr(keep, attr, getattr(drop, attr))

    db.flush()
    db.execute(text("delete from locations where id = :drop"), params)
    db.expire(keep)
    report.rewrite = rewrite_event_keys(db, keep)
    return report
