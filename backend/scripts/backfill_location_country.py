"""Привести locations.country к правде и к русскому языку.

Два прохода:

1. По координатам (Natural Earth 110m) — где точка, там и страна. Название
   берётся из NAME_RU geojson, то есть сразу по-русски. Точку, промахнувшуюся
   мимо всех полигонов (пляжные и островные площадки — контуры 110m грубые),
   относим к ближайшему берегу, но не дальше 1.5°.
2. Строкам без координат приводим к русскому уже записанное название — по
   словарю geojson (ADMIN / NAME / NAME_EN / NAME_LONG → NAME_RU) и по
   app.geo.country_names. Страну при этом НЕ меняем, только язык.

   Исключение — заглушка parkrun: её перевод дал бы не русское название, а
   красивую ложь. Домен parkrun.org.uk — общий вход в мировой каталог, и этой
   заглушкой профильный импорт пометил Якутск, Париж и Дрезден наравне с
   Лондоном (среди безкоординатных «британцев» — Aggeneys и Barberton в ЮАР,
   Ballarat в Австралии, Athlone в Ирландии). Стираем в NULL, то есть «не
   знаем». Страну им вернёт первый проход, когда появятся координаты —
   см. scripts/backfill_parkrun_coordinates.py.

Зачем всё это: на 08.08.2026 в базе одновременно жили «Великобритания» (2143
строки) и «United Kingdom» (367) — одна страна двумя строками в любой
группировке, — и обе половины врали про страну. Заглушку в коде убрали, этот
скрипт разбирает накопленное.

По умолчанию DRY RUN; для записи — флаг --apply.

DEV:  docker compose exec api python scripts/backfill_location_country.py [--apply]
PROD: docker compose exec -e DATABASE_URL="postgresql+psycopg://USER:PASS@host.docker.internal:5434/DB" \
        api python scripts/backfill_location_country.py [--apply]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

from app.geo.country_names import normalize_country_name

GEOJSON_PATH = os.path.join(os.path.dirname(__file__), "ne_110m_countries.geojson")
# Привести к формам, уже принятым в нашей БД / у пользователя.
NAME_OVERRIDE = {"Белоруссия": "Беларусь"}
# НЕ переписываем «Россия» на эти страны (спорные территории — Крым/Донбасс;
# NE-geojson относит их к Украине, но приложение осознанно ставит «Россия»).
SKIP_FLIP_FROM_RUSSIA = {"Украина"}
# Поля geojson с англоязычными названиями — из них строим словарь перевода.
_ALIAS_FIELDS = ("ADMIN", "NAME", "NAME_EN", "NAME_LONG", "BRK_NAME", "NAME_SORT")
# Заглушки, которые не значат страну: их не переводим, а стираем.
PARKRUN_PLATFORM_CODE = "parkrun"
PARKRUN_COUNTRY_STUBS = frozenset({"united kingdom", "великобритания"})


def _rings(geometry: dict) -> list:
    kind = geometry["type"]
    coords = geometry["coordinates"]
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return coords
    return []


def _in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi):
            inside = not inside
        j = i
    return inside


def _contains(lon: float, lat: float, geometry: dict) -> bool:
    for poly in _rings(geometry):
        if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False


def _russian_name(props: dict) -> str:
    name = props.get("NAME_RU") or props.get("ADMIN")
    return NAME_OVERRIDE.get(name, name)


def load_countries() -> list[tuple[str, dict]]:
    data = json.load(open(GEOJSON_PATH, encoding="utf-8"))
    return [(_russian_name(f["properties"]), f["geometry"]) for f in data["features"]]


def load_name_aliases() -> dict[str, str]:
    """Англоязычное название страны (в нижнем регистре) → русское."""
    data = json.load(open(GEOJSON_PATH, encoding="utf-8"))
    aliases: dict[str, str] = {}
    for feature in data["features"]:
        props = feature["properties"]
        russian = _russian_name(props)
        if not russian:
            continue
        for field in _ALIAS_FIELDS:
            value = props.get(field)
            if value:
                aliases.setdefault(str(value).strip().lower(), russian)
    return aliases


def country_of(lat: float, lon: float, countries: list[tuple[str, dict]]) -> str | None:
    for name, geometry in countries:
        if _contains(lon, lat, geometry):
            return name
    return None


# Насколько далеко от берега ещё считаем точку «этой страной», в градусах.
# 110m-контуры грубые: пляжные и островные площадки (Avery Beach, Akrotiri)
# промахиваются мимо всех полигонов на десятки километров.
NEAREST_MAX_DEG = 1.5


def nearest_country(
    lat: float, lon: float, countries: list[tuple[str, dict]], max_deg: float = NEAREST_MAX_DEG
) -> str | None:
    """Ближайшая страна для точки, не попавшей ни в один полигон.

    Считаем по вершинам контуров: точность тут не нужна — нужен ответ «чей это
    берег». Долготу масштабируем по широте, иначе у полюсов побеждает случайный
    сосед. Дальше max_deg не гадаем: пусть лучше останется пусто.
    """
    import math

    scale = math.cos(math.radians(lat)) or 1e-6
    best_name: str | None = None
    best_dist = max_deg * max_deg
    for name, geometry in countries:
        for poly in _rings(geometry):
            for x, y in poly[0]:
                dx = (x - lon) * scale
                dy = y - lat
                dist = dx * dx + dy * dy
                if dist < best_dist:
                    best_dist = dist
                    best_name = name
    return best_name


def _is_russian_text(value: str) -> bool:
    return any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Записать изменения (без флага — только показать)")
    parser.add_argument("--limit", type=int, default=20, help="Сколько примеров печатать в каждом блоке")
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        from app.config import get_settings

        url = get_settings().database_url
    engine = create_engine(url)
    countries = load_countries()
    aliases = load_name_aliases()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT l.id, l.latitude, l.longitude, l.country, p.code "
                "FROM locations l JOIN platforms p ON p.id = l.platform_id"
            )
        ).fetchall()

        # (id, было, стало, платформа)
        by_coords: list[tuple[str, str | None, str | None, str]] = []
        by_nearest: list[tuple[str, str | None, str | None, str]] = []
        by_translation: list[tuple[str, str | None, str | None, str]] = []
        stub_cleared: list[tuple[str, str | None, str | None, str]] = []
        unmatched = 0
        skipped_disputed = 0
        left_untranslated: Counter[str] = Counter()

        for loc_id, lat, lon, current, platform_code in rows:
            resolved = None
            landed_in_polygon = False
            if lat is not None and lon is not None:
                resolved = country_of(float(lat), float(lon), countries)
                landed_in_polygon = resolved is not None
                if resolved is None:
                    unmatched += 1
                    resolved = nearest_country(float(lat), float(lon), countries)

            if resolved is not None:
                if current == "Россия" and resolved in SKIP_FLIP_FROM_RUSSIA:
                    skipped_disputed += 1
                    continue
                if resolved != current:
                    bucket = by_coords if landed_in_polygon else by_nearest
                    bucket.append((str(loc_id), current, resolved, platform_code))
                continue

            if not current:
                continue

            # Координат нет (или они не легли ни к одной стране) — заглушку
            # parkrun проверить нечем, значит «не знаем».
            if platform_code == PARKRUN_PLATFORM_CODE and current.strip().lower() in PARKRUN_COUNTRY_STUBS:
                stub_cleared.append((str(loc_id), current, None, platform_code))
                continue

            # Настоящее значение — приводим только язык.
            translated = aliases.get(current.strip().lower()) or normalize_country_name(current)
            if translated and translated != current:
                by_translation.append((str(loc_id), current, translated, platform_code))
            elif not _is_russian_text(current):
                left_untranslated[current] += 1

        changes = by_coords + by_nearest + by_translation + stub_cleared
        if args.apply:
            for loc_id, _was, now, _code in changes:
                conn.execute(
                    text("UPDATE locations SET country = :c WHERE id = :id"),
                    {"c": now, "id": loc_id},
                )

    print(f"Локаций всего: {len(rows)} | точка вне всех полигонов: {unmatched} | "
          f"пропущено спорных (Россия→Украина): {skipped_disputed}")

    print(f"\n1) По координатам: {len(by_coords)}")
    for name, count in Counter(f"{c} {was!r} → {now!r}" for _, was, now, c in by_coords).most_common(args.limit):
        print(f"   {name}: {count}")
    if len(set(f"{c} {was!r} → {now!r}" for _, was, now, c in by_coords)) > args.limit:
        print("   …")

    print(f"\n2) По ближайшему берегу (точка вне полигонов): {len(by_nearest)}")
    for name, count in Counter(f"{c} {was!r} → {now!r}" for _, was, now, c in by_nearest).most_common(args.limit):
        print(f"   {name}: {count}")

    print(f"\n3) Перевод названия (страна та же): {len(by_translation)}")
    for name, count in Counter(f"{c} {was!r} → {now!r}" for _, was, now, c in by_translation).most_common(args.limit):
        print(f"   {name}: {count}")

    print(f"\n4) Снята заглушка parkrun (координаты не помогли): {len(stub_cleared)}")
    for name, count in Counter(f"{c} {was!r} → NULL" for _, was, _now, c in stub_cleared).most_common(args.limit):
        print(f"   {name}: {count}")
    if stub_cleared:
        print("   вернуть страну этим строкам сможет первый проход, когда появятся")
        print("   координаты — см. scripts/backfill_parkrun_coordinates.py")

    if left_untranslated:
        print("\nОсталось нерусским (перевода нет, не трогаем):")
        for name, count in left_untranslated.most_common():
            print(f"   {name!r}: {count}")

    print(f"\nВсего изменений: {len(changes)}")
    if args.apply:
        print("ПРИМЕНЕНО.")
    else:
        print("DRY RUN — ничего не записано. Для записи добавьте --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
