#!/usr/bin/env python3
"""Добить географию parkrun-локаций по архивному каталогу площадок.

Живой https://images.parkrun.com/events.json знает только ДЕЙСТВУЮЩИЕ площадки,
а у нас в базе живут и закрытые: локации заводятся из профилей участников, и
человек, пробежавший Bois de Boulogne до ухода Франции из parkrun, приносит с
собой площадку, которой в каталоге больше нет. Отсюда две дыры на 10.09.2026:

* 325 parkrun-локаций без страны. Профильный импорт помечал любую площадку мира
  заглушкой «United Kingdom» (parkrun.org.uk — общий вход в каталог), а
  `backfill_location_country.py` эту ложь стёр в NULL, рассчитывая вернуть
  страну по координатам. Живой каталог закрывает из них только 110.
  У 24 локаций есть финиши наших пользователей, и челлендж «Международный
  турист» такие финиши не засчитывает — честно подписывает «Страна площадки
  неизвестна».
* 214 parkrun-локаций без координат: «дальность стартов от дома» считает
  поездку в Лондон нулём километров. Живой каталог закрывает 46.

Дыры закрывает АРХИВ каталога: Wayback Machine хранит events.json с 10.2019.
Объединение живого каталога и 13 снимков (2019-10 … 2026-08) — 3339 площадок
против 2973 живых, и в нём есть ушедшие страны целиком (код 31 — Франция, 79 —
Россия: код исчезает из events.json вместе со страной). Слепок объединения
лежит рядом, в `app/parkrun/data/events_archive.json`, ездит с образом, и
прогон интернета не требует вовсе (`--no-fetch`).

Матчим как `parkrun_catalog_sync.py`: сначала по нормализованному слагу, потом
по названию площадки — наши ключи выкидывают не-ASCII, и «la Ramée» лежит как
`la-ram-e`. Название, указывающее на две разные площадки, из индекса выброшено.

Скрипт только ЗАПОЛНЯЕТ пустое: и страну, и координаты. У российских площадок
координаты выверены руками, перетирать их каталогом нельзя, и то же правило
дешевле держать общим для всех.

Уже проставленную страну скрипт сам не переписывает: countrycode у parkrun —
не география, а подчинение, и Виндхук с Мбабане каталог отдаёт под кодом ЮАР
просто потому, что их обслуживает parkrun.co.za. Автоматическая «починка»
правила бы географию в худшую сторону. Расхождения разобраны руками один раз и
записаны таблицами CATALOG_WINS / DATABASE_WINS ниже; всё, чего в них нет,
уходит в отчёт отдельным блоком и ждёт живого решения.

Живой каталог тут — только освежение слепка, и дотягиваться до
images.parkrun.com прод не обязан: не ответил — работаем по слепку, о чём
скрипт скажет вслух. Чтобы не ждать таймаута зря, на проде проще сразу
`--no-fetch`.

По умолчанию DRY RUN; для записи — флаг --apply.

DEV:  docker compose exec api python scripts/backfill_parkrun_from_archive.py [--apply]
PROD: CONFIRM_PROD=1 make prod-run ARGS="scripts/backfill_parkrun_from_archive.py --no-fetch --apply"

Координаты по действующим площадкам умеет и `backfill_parkrun_coordinates.py`,
но он ходит в SQLite соседнего репозитория parkrun-monitoring (то есть только с
Мака) и закрытых площадок не знает. Этот скрипт покрывает и то, и другое.

Пересобрать слепок (снимки качаются руками):

    curl -s 'https://web.archive.org/cdx/search/cdx?url=images.parkrun.com/events.json&output=json&fl=timestamp&collapse=timestamp:6'
    curl -s --compressed -o snap.json 'https://web.archive.org/web/<TS>id_/https://images.parkrun.com/events.json'
    python scripts/backfill_parkrun_from_archive.py --rebuild-index --events-json snap.json [--events-json …]

Снимки подавайте от свежего к старому: у слепка выигрывает тот источник, что
пришёл первым, а свежие координаты площадки точнее старых.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVENTS_URL = "https://images.parkrun.com/events.json"
USER_AGENT = "saturday-runs-stats/1.0 (+https://run5k.run)"
ARCHIVE_INDEX = ROOT / "app" / "parkrun" / "data" / "events_archive.json"
PLATFORM_CODE = "parkrun"

# countrycode из events.json → страна. Названий каталог не отдаёт, только домен
# страны — он и стоит в комментарии, по нему код и сверяется. Коды 31 и 79
# принадлежат ушедшим из parkrun Франции и России: в живом каталоге их больше
# нет, но в архивных снимках есть, и площадки оттуда нам как раз нужны.
# Русское написание даёт normalize_country_name, поэтому здесь — как в каталоге.
COUNTRY_BY_CODE: dict[int, str] = {
    3: "Australia",  # www.parkrun.com.au
    4: "Austria",  # www.parkrun.co.at
    14: "Canada",  # www.parkrun.ca
    23: "Denmark",  # www.parkrun.dk
    30: "Finland",  # www.parkrun.fi
    31: "France",  # www.parkrun.fr — страна ушла из parkrun
    32: "Germany",  # www.parkrun.com.de
    42: "Ireland",  # www.parkrun.ie
    44: "Italy",  # www.parkrun.it
    46: "Japan",  # www.parkrun.jp
    54: "Lithuania",  # www.parkrun.lt
    57: "Malaysia",  # www.parkrun.my
    64: "Netherlands",  # www.parkrun.co.nl
    65: "New Zealand",  # www.parkrun.co.nz
    67: "Norway",  # www.parkrun.no
    74: "Poland",  # www.parkrun.pl
    79: "Russia",  # www.parkrun.ru — страна ушла из parkrun
    82: "Singapore",  # www.parkrun.sg
    85: "South Africa",  # www.parkrun.co.za
    88: "Sweden",  # www.parkrun.se
    97: "United Kingdom",  # www.parkrun.org.uk
    98: "United States",  # www.parkrun.us
}
# Псевдострана каталога: под нулём parkrun отдаёт мировые итоги, площадок там нет.
GLOBAL_COUNTRY_CODE = 0

# Разбор расхождений «база против каталога» от 10.09.2026, по координатам.
#
# Здесь каталог знает лучше: страну в базу писал координатный проход
# `backfill_location_country.py`, а контуры Natural Earth 110m слишком грубы для
# границ и мелких стран. Сингапур в них отдельным полигоном не выделен вовсе
# (все пять площадок лежат на 1.29–1.36° с. ш., 103.76–103.92° в. д. — это он и
# есть), ирландско-британская граница смазана в обе стороны, а Ахен, Маастрихт
# и Энсхеде стоят друг у друга на пороге. Гернси и Джерси — коронные владения, а
# не Франция: parkrun числит их за Великобританией, и это ближе к правде, чем
# берег, к которому их притянул ближайший полигон.
CATALOG_WINS = frozenset(
    {
        # Сингапур, записанный Малайзией.
        "bayeastgarden",
        "bedokreservoir",
        "bishan",
        "eastcoastpark",
        "westcoastpark",
        # Республика Ирландия, записанная Великобританией.
        "buncrana",  # Донегол
        "castleblayney",  # Монахан
        "cootehill",  # Каван
        "dundalk",  # Лаут
        "monaghantown",  # Монахан
        # Северная Ирландия, записанная Ирландией.
        "enniskillen",  # Фермана
        "holycrosscollege",  # Страбан, Тирон
        # Нормандские острова, записанные Францией.
        "guernsey",
        "jersey",
        # Приграничье, ушедшее к соседу.
        "lousberg",  # Ахен — Германия, а не Бельгия
        "tapijn",  # Маастрихт — Нидерланды, а не Бельгия
        "vanheekpark",  # Энсхеде — Нидерланды, а не Германия
        "kambakugolfclub",  # Коматипоорт — ЮАР, а не Мозамбик
    }
)
# А здесь лучше знаем мы: countrycode у parkrun — не география, а подчинение.
# Намибию, Эсватини и Фолкленды обслуживают чужие домены, и координатная страна
# в базе правдивее каталожной. Значение записано, чтобы расхождение всплыло
# заново, если страну в базе кто-то изменит.
DATABASE_WINS: dict[str, str] = {
    "capepembrokelighthouse": "Фолклендские острова",  # каталог: Великобритания
    "manzini": "Эсватини",  # каталог: ЮАР
    "mbabane": "Эсватини",  # каталог: ЮАР
    "swakopmund": "Намибия",  # каталог: ЮАР
    "walvisbay": "Намибия",  # каталог: ЮАР
    "windhoek": "Намибия",  # каталог: ЮАР
}


class CatalogEvent(NamedTuple):
    country_code: int
    latitude: float
    longitude: float


def normalize_name(value: str | None) -> str:
    """Название площадки без регистра и разделителей: «Abbey Park» → «abbeypark».

    Совпадает с `parkrun_catalog_sync.py` — индекс слепка строится тем же ключом.
    """
    if not value:
        return ""
    return re.sub(r"[^0-9a-zа-яё]+", "", value.strip().lower())


class ArchiveCatalog:
    """Площадки parkrun из живого каталога и его архивных снимков.

    Источники складываются по старшинству: кто пришёл первым, тот и прав, —
    поэтому подавать их надо от свежего к старому.
    """

    def __init__(self) -> None:
        self.events: dict[str, CatalogEvent] = {}
        # Название → слаг. None значит «название встречалось у разных площадок»:
        # такое не подсказка, а ловушка, и по нему мы не отвечаем.
        self.slug_by_name: dict[str, str | None] = {}

    def add_event(self, slug: str, event: CatalogEvent) -> None:
        if slug:
            self.events.setdefault(slug, event)

    def add_name(self, name: str, slug: str) -> None:
        if not name or not slug:
            return
        known = self.slug_by_name.get(name, ...)
        if known is ...:
            self.slug_by_name[name] = slug
        elif known != slug:
            self.slug_by_name[name] = None

    def add_catalog(self, payload: dict[str, Any]) -> int:
        added = 0
        for feature in (payload.get("events") or {}).get("features") or []:
            properties = feature.get("properties") or {}
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            code = properties.get("countrycode")
            slug = normalize_name(str(properties.get("eventname") or ""))
            if not slug or len(coordinates) < 2:
                continue
            if not isinstance(code, int) or code == GLOBAL_COUNTRY_CODE:
                continue
            self.add_event(slug, CatalogEvent(code, float(coordinates[1]), float(coordinates[0])))
            self.add_name(normalize_name(str(properties.get("EventShortName") or "")), slug)
            added += 1
        return added

    def add_dump(self, payload: dict[str, Any]) -> None:
        for slug, row in (payload.get("events") or {}).items():
            code, latitude, longitude = row
            self.add_event(slug, CatalogEvent(int(code), float(latitude), float(longitude)))
        for name, slug in (payload.get("names") or {}).items():
            self.add_name(name, slug)

    def lookup(self, external_key: str | None, name: str | None) -> CatalogEvent | None:
        event = self.events.get(normalize_name(external_key))
        if event is not None:
            return event
        slug = self.slug_by_name.get(normalize_name(name))
        return self.events.get(slug) if slug else None

    def as_dump(self) -> dict[str, Any]:
        # Название, совпавшее со слагом площадки, в слепок не пишем: поиск и так
        # начинается со слагов, такая строка недостижима. Это половина файла.
        return {
            "events": {slug: list(event) for slug, event in sorted(self.events.items())},
            "names": {
                name: slug
                for name, slug in sorted(self.slug_by_name.items())
                if slug is not None and name not in self.events
            },
        }


def dump_text(dump: dict[str, Any]) -> str:
    """JSON слепка: одна площадка — одна строка, чтобы диффы читались."""
    lines = ["{", '"events": {']
    rows = [f"{json.dumps(slug)}: {json.dumps(event, ensure_ascii=False)}" for slug, event in dump["events"].items()]
    lines.append(",\n".join(rows))
    lines.append("},")
    lines.append('"names": {')
    rows = [f"{json.dumps(name, ensure_ascii=False)}: {json.dumps(slug)}" for name, slug in dump["names"].items()]
    lines.append(",\n".join(rows))
    lines.append("}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def fetch_catalog() -> dict[str, Any]:
    request = urllib.request.Request(EVENTS_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def build_catalog(args: argparse.Namespace) -> tuple[ArchiveCatalog, list[str]]:
    catalog = ArchiveCatalog()
    sources: list[str] = []

    if not args.no_fetch:
        try:
            count = catalog.add_catalog(fetch_catalog())
        except (OSError, ValueError) as exc:
            # Слепок и так покрывает всё, что покрывает живой каталог, — падать
            # из-за недоступного images.parkrun.com незачем.
            print(f"Живой каталог недоступен ({exc}), работаем по слепку", file=sys.stderr)
        else:
            sources.append(f"живой каталог ({count} событий)")

    for path in args.events_json:
        count = catalog.add_catalog(json.loads(Path(path).read_text(encoding="utf-8")))
        sources.append(f"{path} ({count} событий)")

    if ARCHIVE_INDEX.exists():
        catalog.add_dump(json.loads(ARCHIVE_INDEX.read_text(encoding="utf-8")))
        sources.append(f"слепок {ARCHIVE_INDEX.name}")

    return catalog, sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Записать изменения (без флага — только показать)")
    parser.add_argument(
        "--events-json",
        action="append",
        default=[],
        metavar="PATH",
        help="Локальная копия events.json (в том числе архивный снимок), можно повторять",
    )
    parser.add_argument("--no-fetch", action="store_true", help="Не ходить в сеть, обойтись слепком и --events-json")
    parser.add_argument("--rebuild-index", action="store_true", help="Только пересобрать слепок, в БД не ходить")
    parser.add_argument("--limit", type=int, default=40, help="Сколько строк печатать в каждом блоке (0 — все)")
    args = parser.parse_args()

    catalog, sources = build_catalog(args)
    unique_names = sum(1 for slug in catalog.slug_by_name.values() if slug)
    print("Каталог собран из:", "; ".join(sources) or "ничего")
    print(f"  площадок: {len(catalog.events)} | однозначных названий: {unique_names}")

    if args.rebuild_index:
        ARCHIVE_INDEX.parent.mkdir(parents=True, exist_ok=True)
        dump = catalog.as_dump()
        ARCHIVE_INDEX.write_text(dump_text(dump), encoding="utf-8")
        print(f"\nСлепок записан: {ARCHIVE_INDEX} ({len(dump['events'])} площадок, {len(dump['names'])} названий)")
        return 0

    # Пересборка слепка — работа с одним каталогом, ей ни БД, ни приложение не
    # нужны; поэтому импорты приложения живут здесь, а не наверху.
    from sqlalchemy import select

    from app.db.session import get_session_factory
    from app.geo.country_names import normalize_country_name
    from app.migration.lookups import ParkrunLookups
    from app.models import Location, Platform

    unknown_codes: Counter[int] = Counter()

    def country_of(event: CatalogEvent) -> str | None:
        name = COUNTRY_BY_CODE.get(event.country_code)
        if name is None:
            unknown_codes[event.country_code] += 1
            return None
        return normalize_country_name(name)

    session_factory = get_session_factory()
    with session_factory() as db:
        locations = db.execute(
            select(Location)
            .join(Platform, Location.platform_id == Platform.id)
            .where(Platform.code == PLATFORM_CODE)
            .order_by(Location.external_key)
        ).scalars().all()

        countries: list[tuple[str, str, str]] = []
        coordinates: list[tuple[str, str, str]] = []
        unresolved: list[tuple[str, str]] = []
        corrected: list[tuple[str, str, str]] = []
        mismatched: list[tuple[str, str, str]] = []
        country_counter: Counter[str] = Counter()
        country_known = 0
        coordinates_known = 0
        kept_by_review = 0

        for location in locations:
            # Псевдоплощадка сводки ролей — не место на карте: ни страны, ни точки.
            if location.external_key == ParkrunLookups.PARKRUN_SUMMARY_SLUG:
                continue

            has_coordinates = location.latitude is not None and location.longitude is not None
            country_known += bool((location.country or "").strip())
            coordinates_known += has_coordinates

            event = catalog.lookup(location.external_key, location.name)
            if event is None:
                if not (location.country or "").strip() or not has_coordinates:
                    unresolved.append((location.external_key, location.name or ""))
                continue

            country = country_of(event)
            current = (location.country or "").strip()
            if not current:
                if country is not None:
                    countries.append((location.external_key, location.name or "", country))
                    country_counter[country] += 1
                    if args.apply:
                        location.country = country
            elif country is not None and country != current:
                if location.external_key in CATALOG_WINS:
                    corrected.append((location.external_key, current, country))
                    if args.apply:
                        location.country = country
                elif DATABASE_WINS.get(location.external_key) == current:
                    kept_by_review += 1
                else:
                    mismatched.append((location.external_key, current, country))

            if not has_coordinates:
                coordinates.append(
                    (location.external_key, location.name or "", f"{event.latitude:.5f}, {event.longitude:.5f}")
                )
                if args.apply:
                    location.latitude = event.latitude
                    location.longitude = event.longitude

        if args.apply:
            db.commit()

    def head(rows: list) -> list:
        return rows if args.limit <= 0 else rows[: args.limit]

    def block(title: str, rows: list, render) -> None:
        print(f"\n{title}: {len(rows)}")
        for row in head(rows):
            print(f"   {render(row)}")
        hidden = len(rows) - len(head(rows))
        if hidden:
            print(f"   … и ещё {hidden}")

    block("1) Проставим страну (было пусто)", countries, lambda r: f"{r[0]} ({r[1]}): {r[2]}")
    if country_counter:
        print("   итого:", ", ".join(f"{k} — {v}" for k, v in country_counter.most_common()))

    block("2) Проставим координаты (было пусто)", coordinates, lambda r: f"{r[0]} ({r[1]}): {r[2]}")

    block(
        "3) Поправим страну (расхождение разобрано, каталог прав)",
        corrected,
        lambda r: f"{r[0]}: «{r[1]}» → «{r[2]}»",
    )

    block("4) Площадки нет и в архиве каталога", unresolved, lambda r: f"{r[0]} ({r[1]})")

    block(
        "5) НОВОЕ расхождение с каталогом (НЕ трогаем, разобрать руками)",
        mismatched,
        lambda r: f"{r[0]}: в базе «{r[1]}», каталог «{r[2]}»",
    )
    print(f"   разобрано раньше, оставлено как в базе: {kept_by_review}")

    if unknown_codes:
        print("\nНеизвестные countrycode (добавь в COUNTRY_BY_CODE):")
        for code, count in unknown_codes.most_common():
            print(f"   {code}: {count} площадок")

    print(f"\nparkrun-локаций всего: {len(locations)}")
    print(f"  страна была известна: {country_known} | координаты были известны: {coordinates_known}")
    if args.apply:
        print("ПРИМЕНЕНО.")
    else:
        print("DRY RUN — ничего не записано. Для записи добавьте --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
