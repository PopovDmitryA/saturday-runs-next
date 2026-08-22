#!/usr/bin/env python3
"""Сверка parkrun-локаций с официальным каталогом площадок.

Источник — https://images.parkrun.com/events.json: официальный GeoJSON parkrun
со всеми действующими площадками мира (≈2955 на 11.08.2026). В нём есть ровно
то, чего нам не хватает: официальный слаг (eventname), координаты старта, домен
страны и населённый пункт.

Зачем это понадобилось (репорт пользователя через Дмитрия, 11.08.2026 — площадка
Küchenholz): слаг parkrun-локации мы выводим из названия в парсере профиля
(`app/parkrun/parsers/athlete.py`), а он выкидывает всё не-ASCII. «Küchenholz»
превращался в `k-chenholz`, и дальше ломалось всё, что строится от слага:

* ссылка на площадку — `parkrun.org.uk/<slug>/`, то есть 404 (а зарубежные
  площадки UK-домен не отдаёт вовсе, нужен домен страны);
* координаты — прежний бэкфилл `backfill_parkrun_coordinates.py` матчит
  каталог по слагу без дефисов, и как раз кривые слаги мимо него проходят.

Скрипт матчит наши локации с каталогом сначала по нормализованному слагу, потом
по названию площадки, и приводит в порядок:

* координаты — только там, где их нет (у российских выверены руками);
* source_url — на реальную страницу площадки (домен страны + официальный слаг);
* external_key — по флагу --rename-keys: наш ключ становится официальным слагом,
  чтобы парсер профилей и каталог сошлись на одном значении. Ключ переносится
  вместе со ссылками в location_catalog_links. Если официальный слаг уже занят
  другой строкой (такое есть: «Angarskie Prudy» лежит и как `angarskie-prudy`,
  и как `angarskieprudy`), строку пропускаем — это случай для слияния руками.

Город скрипт сам не трогает: у нас города по-русски, а в каталоге — на местном
языке, автоматическая замена всё бы испортила. Точечные правки — через
--set-city (например, тому же Küchenholz каталог говорит Leipzig, а у нас в базе
стоял Берлин).

Запуск:

    make prod-run ARGS="scripts/parkrun_catalog_sync.py --dry-run --pretty"
    CONFIRM_PROD=1 make prod-run ARGS="scripts/parkrun_catalog_sync.py --rename-keys"

Дамп каталога для парсера профилей (лежит в пакете, чтобы ездил с образом):

    python backend/scripts/parkrun_catalog_sync.py --dump-catalog
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import Location, LocationCatalogLink, Platform  # noqa: E402

EVENTS_URL = "https://images.parkrun.com/events.json"
# Каталог для парсера профилей: только имя → слаг, без координат и стран.
CATALOG_DUMP = ROOT / "app" / "parkrun" / "data" / "events_slugs.json"
USER_AGENT = "saturday-runs-stats/1.0 (+https://run5k.run)"


def normalize_name(value: str | None) -> str:
    """Название площадки без регистра и разделителей: «Abbey Park» → «abbeypark»."""
    if not value:
        return ""
    return re.sub(r"[^0-9a-zа-яё]+", "", value.strip().lower())


def fetch_catalog(source: Path | None) -> dict[str, Any]:
    if source is not None:
        return json.loads(source.read_text(encoding="utf-8"))
    request = urllib.request.Request(EVENTS_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


class CatalogEvent:
    __slots__ = ("slug", "name", "latitude", "longitude", "domain", "place")

    def __init__(self, slug: str, name: str, latitude: float, longitude: float, domain: str, place: str):
        self.slug = slug
        self.name = name
        self.latitude = latitude
        self.longitude = longitude
        self.domain = domain
        self.place = place

    @property
    def page_url(self) -> str | None:
        if not self.domain:
            return None
        return f"https://{self.domain}/{self.slug}/"


def parse_catalog(payload: dict[str, Any]) -> list[CatalogEvent]:
    countries = payload.get("countries") or {}
    events: list[CatalogEvent] = []
    for feature in (payload.get("events") or {}).get("features") or []:
        properties = feature.get("properties") or {}
        slug = str(properties.get("eventname") or "").strip()
        name = str(properties.get("EventShortName") or "").strip()
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        if not slug or not name or len(coordinates) < 2:
            continue
        country = countries.get(str(properties.get("countrycode"))) or {}
        events.append(
            CatalogEvent(
                slug=slug,
                name=name,
                latitude=float(coordinates[1]),
                longitude=float(coordinates[0]),
                domain=str(country.get("url") or "").strip(),
                place=str(properties.get("EventLocation") or "").strip(),
            )
        )
    return events


def build_index(events: list[CatalogEvent]) -> tuple[dict[str, CatalogEvent], dict[str, CatalogEvent]]:
    """Индексы «по слагу» и «по имени». Неоднозначные имена выкидываем."""
    by_slug: dict[str, CatalogEvent] = {}
    by_name: dict[str, CatalogEvent | None] = {}
    for event in events:
        by_slug[normalize_name(event.slug)] = event
        key = normalize_name(event.name)
        by_name[key] = None if key in by_name else event
    return by_slug, {key: value for key, value in by_name.items() if value is not None}


def parse_city_overrides(values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in values:
        slug, _, city = item.partition("=")
        if not slug or not city:
            raise SystemExit(f"--set-city ожидает формат slug=Город, получено: {item}")
        overrides[normalize_name(slug)] = city
    return overrides


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync parkrun locations with the official catalog")
    parser.add_argument("--events-json", type=Path, default=None, help="Локальная копия events.json")
    parser.add_argument("--dry-run", action="store_true", help="Ничего не писать, только посчитать")
    parser.add_argument("--rename-keys", action="store_true", help="Переименовать external_key в официальный слаг")
    parser.add_argument(
        "--set-city",
        action="append",
        default=[],
        metavar="SLUG=ГОРОД",
        help="Точечно поправить город (слаг — наш или официальный), можно повторять",
    )
    parser.add_argument("--dump-catalog", action="store_true", help="Только сохранить каталог имён для парсера")
    parser.add_argument("--limit", type=int, default=0, help="Максимум строк за прогон (0 — все)")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    payload = fetch_catalog(args.events_json)
    events = parse_catalog(payload)
    if not events:
        print(json.dumps({"error": "каталог пуст"}, ensure_ascii=False), file=sys.stderr)
        return 1
    by_slug, by_name = build_index(events)

    if args.dump_catalog:
        CATALOG_DUMP.parent.mkdir(parents=True, exist_ok=True)
        dump = {normalize_name(event.name): event.slug for event in events}
        CATALOG_DUMP.write_text(
            json.dumps(dump, ensure_ascii=False, sort_keys=True, indent=0) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"catalog_dump": str(CATALOG_DUMP), "events": len(dump)}, ensure_ascii=False))
        return 0

    from app.db.session import get_session_factory

    city_overrides = parse_city_overrides(args.set_city)

    db = get_session_factory()()
    stats = {
        "locations_total": 0,
        "matched": 0,
        "unmatched": 0,
        "coordinates_set": 0,
        "source_url_set": 0,
        "keys_renamed": 0,
        "keys_conflicted": 0,
        "cities_set": 0,
    }
    conflicts: list[str] = []
    samples: list[dict[str, str]] = []
    try:
        query = (
            db.query(Location)
            .join(Platform, Location.platform_id == Platform.id)
            .filter(Platform.code == "parkrun")
            .order_by(Location.external_key.asc())
        )
        if args.limit:
            query = query.limit(args.limit)
        locations = query.all()
        taken_keys = {location.external_key for location in locations}

        for location in locations:
            stats["locations_total"] += 1
            event = by_slug.get(normalize_name(location.external_key)) or by_name.get(
                normalize_name(location.name)
            )
            if event is None:
                stats["unmatched"] += 1
                continue
            stats["matched"] += 1
            changes: list[str] = []

            if location.latitude is None or location.longitude is None:
                location.latitude = event.latitude
                location.longitude = event.longitude
                stats["coordinates_set"] += 1
                changes.append(f"координаты {event.latitude:.5f},{event.longitude:.5f}")

            page_url = event.page_url
            if page_url and location.source_url != page_url:
                location.source_url = page_url
                stats["source_url_set"] += 1
                changes.append(f"ссылка {page_url}")

            city = city_overrides.get(normalize_name(location.external_key)) or city_overrides.get(
                normalize_name(event.slug)
            )
            if city and location.city != city:
                location.city = city
                stats["cities_set"] += 1
                changes.append(f"город {city}")

            if args.rename_keys and location.external_key != event.slug:
                if event.slug in taken_keys:
                    stats["keys_conflicted"] += 1
                    conflicts.append(f"{location.external_key} → {event.slug}")
                else:
                    old_key = location.external_key
                    taken_keys.discard(old_key)
                    taken_keys.add(event.slug)
                    location.external_key = event.slug
                    # Каталог локаций ссылается на площадку тем же ключом —
                    # без этого связка распадётся и площадка выпадет с карты.
                    db.query(LocationCatalogLink).filter(
                        LocationCatalogLink.platform_id == location.platform_id,
                        LocationCatalogLink.external_key == old_key,
                    ).update({"external_key": event.slug}, synchronize_session=False)
                    stats["keys_renamed"] += 1
                    changes.append(f"ключ {old_key} → {event.slug}")

            if changes and len(samples) < 25:
                samples.append({"location": location.name, "changes": "; ".join(changes)})

        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception as exc:  # noqa: BLE001 — наружу отдаём как JSON
        db.rollback()
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    result = {
        "catalog_events": len(events),
        "dry_run": args.dry_run,
        **stats,
        "conflicts": conflicts[:20],
        "samples": samples,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
