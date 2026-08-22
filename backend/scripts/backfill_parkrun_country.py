#!/usr/bin/env python3
"""Проставить parkrun-локациям страну и город по системе-преемнику.

У parkrun-строк страна почти всегда стаб «United Kingdom»: локации приезжали из
мирового каталога parkrun.org.uk. Из 150 сцепленных с нашими площадками так
помечены 102 — включая Плотинку, Битцу и Чертаново.

Если площадка живёт в 5 вёрстах / s95 / runpark, её география известна: берём
страну и город у связки по каталожной идентичности. Строки без преемника не
трогаем — там наследовать не от чего.

Карта и каталог не зависят от этого прогона (они и так ходят по связке, см.
is_russian_historic), но остальные потребители читают locations.country напрямую,
и там сейчас ложь.

По умолчанию только показывает, что изменится. Запись — с --apply.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.db.session import get_session_factory
from app.models import Location, Platform
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_map_service import MAP_HISTORIC_PLATFORM, MAP_LIVE_PLATFORMS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Записать изменения (без флага — только показать)")
    parser.add_argument("--limit", type=int, default=0, help="Показать не больше N строк в отчёте (0 — все)")
    args = parser.parse_args()

    session_factory = get_session_factory()
    with session_factory() as db:
        catalog_index = LocationCatalogIndex(db)

        live_by_identity: dict[str, Location] = {}
        for location, code in db.execute(
            select(Location, Platform.code)
            .join(Platform, Location.platform_id == Platform.id)
            .where(Platform.code.in_(MAP_LIVE_PLATFORMS))
        ).all():
            key = catalog_index.canonical_identity_key(location, code)
            current = live_by_identity.get(key)
            # Предпочитаем связку, у которой география вообще заполнена.
            if current is None or (not current.country and location.country):
                live_by_identity[key] = location

        historic = db.execute(
            select(Location)
            .join(Platform, Location.platform_id == Platform.id)
            .where(Platform.code == MAP_HISTORIC_PLATFORM)
        ).scalars().all()

        changed: list[str] = []
        no_sibling = 0
        already_ok = 0
        from_counter: Counter[str] = Counter()

        for location in historic:
            key = catalog_index.canonical_identity_key(location, MAP_HISTORIC_PLATFORM)
            sibling = live_by_identity.get(key)
            if sibling is None:
                no_sibling += 1
                continue

            updates: list[str] = []
            if sibling.country and location.country != sibling.country:
                from_counter[str(location.country)] += 1
                updates.append(f"страна «{location.country}» → «{sibling.country}»")
                if args.apply:
                    location.country = sibling.country
            if sibling.city and location.city != sibling.city:
                updates.append(f"город «{location.city}» → «{sibling.city}»")
                if args.apply:
                    location.city = sibling.city
            if sibling.region and location.region != sibling.region:
                updates.append(f"регион «{location.region}» → «{sibling.region}»")
                if args.apply:
                    location.region = sibling.region

            if updates:
                changed.append(f"  {location.external_key}: " + "; ".join(updates))
            else:
                already_ok += 1

        if args.apply:
            db.commit()

    shown = changed if args.limit <= 0 else changed[: args.limit]
    for line in shown:
        print(line)
    if len(changed) > len(shown):
        print(f"  … и ещё {len(changed) - len(shown)}")

    print(f"\nparkrun-локаций всего: {len(historic)}")
    print(f"  со связкой, требуют правки: {len(changed)}")
    print(f"  со связкой, уже верны: {already_ok}")
    print(f"  без связки (не трогаем): {no_sibling}")
    if from_counter:
        print("  страна была:", ", ".join(f"{k} — {v}" for k, v in from_counter.most_common()))
    if not args.apply:
        print("\nпробный прогон, ничего не записано — добавь --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
