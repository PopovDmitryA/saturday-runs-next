"""Определить страну локаций по координатам (Natural Earth 110m) и записать в
locations.country. По умолчанию DRY RUN; для записи — флаг --apply.

DEV:  docker compose exec api python scripts/backfill_location_country.py [--apply]
PROD: docker compose exec -e DATABASE_URL="postgresql+psycopg://USER:PASS@host.docker.internal:5434/DB" \
        api python scripts/backfill_location_country.py [--apply]

Страна берётся из NAME_RU geojson (русские названия), с override под принятые в
БД формы (Белоруссия→Беларусь). Точки, не попавшие ни в одну страну, не трогаем.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

from sqlalchemy import create_engine, text

GEOJSON_PATH = os.path.join(os.path.dirname(__file__), "ne_110m_countries.geojson")
# Привести к формам, уже принятым в нашей БД / у пользователя.
NAME_OVERRIDE = {"Белоруссия": "Беларусь"}
# НЕ переписываем «Россия» на эти страны (спорные территории — Крым/Донбасс;
# NE-geojson относит их к Украине, но приложение осознанно ставит «Россия»).
SKIP_FLIP_FROM_RUSSIA = {"Украина"}


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


def load_countries() -> list[tuple[str, dict]]:
    data = json.load(open(GEOJSON_PATH, encoding="utf-8"))
    out: list[tuple[str, dict]] = []
    for feature in data["features"]:
        props = feature["properties"]
        name = props.get("NAME_RU") or props.get("ADMIN")
        name = NAME_OVERRIDE.get(name, name)
        out.append((name, feature["geometry"]))
    return out


def country_of(lat: float, lon: float, countries: list[tuple[str, dict]]) -> str | None:
    for name, geometry in countries:
        if _contains(lon, lat, geometry):
            return name
    return None


def main() -> None:
    apply = "--apply" in sys.argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        from app.config import get_settings

        url = get_settings().database_url
    engine = create_engine(url)
    countries = load_countries()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, latitude, longitude, country FROM locations "
                "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
            )
        ).fetchall()

        changes: list[tuple[str, str | None, str]] = []
        unmatched = 0
        skipped_disputed = 0
        for loc_id, lat, lon, current in rows:
            resolved = country_of(float(lat), float(lon), countries)
            if resolved is None:
                unmatched += 1
                continue
            if current == "Россия" and resolved in SKIP_FLIP_FROM_RUSSIA:
                skipped_disputed += 1
                continue
            if resolved != current:
                changes.append((str(loc_id), current, resolved))

        print(
            f"Локаций с координатами: {len(rows)} | без совпадения: {unmatched} | "
            f"пропущено спорных (Россия→Украина): {skipped_disputed}"
        )
        print(f"Изменений: {len(changes)}")
        by_target = Counter(c[2] for c in changes)
        print("По странам (назначаем):")
        for name, cnt in by_target.most_common():
            print(f"  {name}: {cnt}")
        # Подозрительное: переписываем НЕ-null и НЕ-'Россия' на другое.
        risky = [c for c in changes if c[1] and c[1] != c[2]]
        if risky:
            print(f"ВНИМАНИЕ, перезапись непустой страны ({len(risky)}):")
            for loc_id, was, now in risky[:20]:
                print(f"  {loc_id}: {was!r} -> {now!r}")

        if apply:
            for loc_id, _was, now in changes:
                conn.execute(
                    text("UPDATE locations SET country = :c WHERE id = :id"),
                    {"c": now, "id": loc_id},
                )
            print(f"ПРИМЕНЕНО: {len(changes)} обновлений.")
        else:
            print("DRY RUN — ничего не записано. Для записи добавьте --apply.")


if __name__ == "__main__":
    main()
