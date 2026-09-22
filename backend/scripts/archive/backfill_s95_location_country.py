#!/usr/bin/env python3
"""Проставить s95-локациям страну по домену реестра.

Страну сама s95 нигде не отдаёт, а домен её задаёт однозначно: s95.rs — Сербия,
s95.by — Беларусь, s95.ru — Россия. Синк локаций страну не заполнял вовсе, а
upsert_location пустым значением не затирает уже известное, — поэтому Белград и
Гродно так и стояли «Россией», хотя город и регион геокод определил верно.

Синк локаций (s95_locations_registry) теперь чинит это сам, но ходит раз в три
дня. Скрипт — чтобы поправить сразу после деплоя, не дожидаясь прогона.

По умолчанию только показывает, что изменится. Запись — с --apply.

DEV:  docker compose exec api python scripts/backfill_s95_location_country.py [--apply]
PROD: docker compose exec api python scripts/backfill_s95_location_country.py [--apply]
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
from app.migration.helpers import s95_country_from_url
from app.models import Location, Platform

PLATFORM_CODE = "s95"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Записать изменения (без флага — только показать)")
    args = parser.parse_args()

    session_factory = get_session_factory()
    with session_factory() as db:
        locations = db.execute(
            select(Location)
            .join(Platform, Location.platform_id == Platform.id)
            .where(Platform.code == PLATFORM_CODE)
            .order_by(Location.external_key)
        ).scalars().all()

        changed: list[str] = []
        already_ok = 0
        no_source_url = 0
        target_counter: Counter[str] = Counter()

        for location in locations:
            if not location.source_url:
                # Домена нет — угадывать нечего, «Россия» по умолчанию тут была бы
                # тем же враньём, из-за которого всё и затевалось.
                no_source_url += 1
                continue
            country = s95_country_from_url(location.source_url)
            if location.country == country:
                already_ok += 1
                continue
            changed.append(f"  {location.external_key} ({location.name}): «{location.country}» → «{country}»")
            target_counter[country] += 1
            if args.apply:
                location.country = country

        if args.apply:
            db.commit()

    for line in changed:
        print(line)

    print(f"\ns95-локаций всего: {len(locations)}")
    print(f"  требуют правки: {len(changed)}")
    print(f"  уже верны: {already_ok}")
    print(f"  без source_url (не трогаем): {no_source_url}")
    if target_counter:
        print("  назначаем:", ", ".join(f"{k} — {v}" for k, v in target_counter.most_common()))
    if not args.apply:
        print("\nпробный прогон, ничего не записано — добавь --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
