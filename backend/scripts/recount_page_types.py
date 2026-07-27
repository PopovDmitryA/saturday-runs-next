#!/usr/bin/env python3
"""Пересчитать page_type у уже записанных просмотров по сохранённому пути.

page_type пишется в момент события, поэтому расширение классификатора старые
строки не трогает: разделы, которых он раньше не знал, продолжают числиться
«прочим», пока не выпадут из выбранного периода. Скрипт приводит историю к
текущим правилам — путь в page_view_events.path сохранён полностью, так что
пересчёт идёт из того же источника, что и запись.

Заодно пересчитываются агрегаты page_stats_daily за задетые дни: отчёт в
админке читает их, а не сырые события.

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
from app.models import PageViewEvent
from app.services.page_analytics_service import classify_page, rollup_day


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Записать изменения (без флага — только показать)")
    parser.add_argument(
        "--only-other",
        action="store_true",
        help="Трогать только строки, числящиеся «прочим» (безопаснее: ничего уже разложенного не двигаем)",
    )
    args = parser.parse_args()

    moves: Counter[tuple[str, str]] = Counter()
    touched_days: set[object] = set()
    changed = 0

    session_factory = get_session_factory()
    with session_factory() as db:
        query = select(PageViewEvent)
        if args.only_other:
            query = query.where(PageViewEvent.page_type == "other")

        for event in db.execute(query).scalars():
            page_type, entity_key = classify_page(event.path)
            if page_type == event.page_type and entity_key == (event.entity_key or ""):
                continue
            moves[(event.page_type, page_type)] += 1
            touched_days.add(event.ts.date())
            changed += 1
            if args.apply:
                event.page_type = page_type
                # entity_key профилей на записи доресолвливается до user_id;
                # здесь оставляем сырой ключ из пути — в отчёте он резолвится
                # так же, как у остальных строк.
                event.entity_key = entity_key

        if args.apply:
            db.commit()
            # Агрегаты за задетые дни пересобираем: админка читает их.
            for day in sorted(touched_days):
                rollup_day(db, day)
            db.commit()

    print(f"строк под пересчёт: {changed}, задетых дней: {len(touched_days)}")
    if moves:
        print("\nкуда переезжают:")
        for (was, now), count in moves.most_common():
            print(f"  {was:<16} → {now:<20} {count}")
    if not args.apply:
        print("\nпробный прогон, ничего не записано — добавь --apply")
    else:
        print("\nагрегаты page_stats_daily за эти дни пересобраны")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
