"""Проставить locations.tz_offset_moscow там, где он пустой.

Зачем: пояса импортированы из легаси (`general_location.tz_from_moscow`), а
легаси знало не все площадки — у 24 из 220 точек 5 вёрст смещение осталось
NULL. Кабинет организатора при NULL считает пояс нулевым, и у восточных
площадок задержка выгрузки протокола уезжает на величину пояса: у Ангарска
(+5) получались отрицательные задержки — «протокол опубликован до старта».

Откуда берём значение: из самих данных. Среди 196 заполненных площадок регион
однозначно задаёт смещение — ни у одного региона нет двух разных значений.
Поэтому справочник строится запросом, а не забивается руками, и не устаревает.
Регионы без единого заполненного образца — в FALLBACK_BY_REGION ниже.

Регион с РАЗНЫМИ смещениями у заполненных площадок (в РФ так бывает: Якутия,
Сахалинская) не угадывается: такая площадка пропускается с предупреждением,
её пояс нужно проставить руками.

По умолчанию — сухой прогон. Запись только с `--apply`.

Запуск (изнутри контейнера api):
    python scripts/backfill_location_tz_offsets.py [--apply]
"""

from __future__ import annotations

import argparse
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import get_session_factory  # noqa: E402
from app.models import Location, Platform  # noqa: E402

# Регионы, где среди заполненных площадок образца нет вовсе. Все — московское
# время (UTC+3), смещение 0; добавлять сюда только бесспорные случаи.
FALLBACK_BY_REGION: dict[str, int] = {
    "Брянская": 0,
    "Орловская": 0,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="писать в базу (иначе сухой прогон)")
    args = parser.parse_args()

    session_factory = get_session_factory()
    with session_factory() as db:
        known = db.execute(
            sa.select(
                Location.region,
                sa.func.array_agg(sa.distinct(Location.tz_offset_moscow)),
            )
            .join(Platform, Platform.id == Location.platform_id)
            .where(
                Platform.code == "five_verst",
                Location.tz_offset_moscow.is_not(None),
                Location.region.is_not(None),
            )
            .group_by(Location.region)
        ).all()

        by_region: dict[str, int] = {}
        ambiguous: dict[str, list[int]] = {}
        for region, offsets in known:
            values = sorted(int(value) for value in offsets)
            if len(values) == 1:
                by_region[region] = values[0]
            else:
                ambiguous[region] = values

        missing = db.execute(
            sa.select(Location)
            .join(Platform, Platform.id == Location.platform_id)
            .where(Platform.code == "five_verst", Location.tz_offset_moscow.is_(None))
            .order_by(Location.name)
        ).scalars().all()

        updated = 0
        unresolved: list[Location] = []
        for location in missing:
            region = location.region or ""
            if region in ambiguous:
                unresolved.append(location)
                continue
            offset = by_region.get(region, FALLBACK_BY_REGION.get(region))
            if offset is None:
                unresolved.append(location)
                continue
            source = "по региону" if region in by_region else "вручную"
            print(f"  {location.name} ({location.external_key}), {region}: {offset:+d} — {source}")
            if args.apply:
                location.tz_offset_moscow = offset
            updated += 1

        if args.apply:
            db.commit()

        print(f"\nБез пояса было: {len(missing)}, проставлено: {updated}")
        if ambiguous:
            print(f"Многопоясные регионы (не угадываем): {ambiguous}")
        if unresolved:
            print("Остались без пояса — проставить руками:")
            for location in unresolved:
                print(f"  {location.name} ({location.external_key}), регион {location.region!r}")
        if not args.apply:
            print("Сухой прогон — ничего не записано. Повторить с --apply.")


if __name__ == "__main__":
    main()
