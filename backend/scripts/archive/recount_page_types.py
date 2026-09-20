#!/usr/bin/env python3
"""Пересчитать page_type у уже записанных просмотров по сохранённому пути.

page_type пишется в момент события, поэтому расширение классификатора старые
строки не трогает: разделы, которых он раньше не знал, продолжают числиться
«прочим», пока не выпадут из выбранного периода. Скрипт приводит историю к
текущим правилам — путь в page_view_events.path сохранён полностью, так что
пересчёт идёт из того же источника, что и запись.

Заодно пересчитываются агрегаты page_stats_daily за задетые дни: отчёт в
админке читает их, а не сырые события.

Пересчёт намеренно не сплошной — см. RECOUNTABLE_TYPES: часть типов по пути
восстановить нельзя, и слепой проход по всей таблице их бы испортил.

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
from app.services.profile_slug_service import resolve_profile_handle

# Пересчитываем ТОЛЬКО те типы, которые расширение классификатора и должно было
# забрать. Полный проход по таблице недопустим:
#   - landing/login/about — исторические типы старых страниц до портала, по пути
#     они превратились бы в portal_* и различие «до/после» пропало бы;
#   - blog_post_click — не просмотр страницы, а клик по посту; классификация по
#     пути уничтожила бы эти события.
RECOUNTABLE_TYPES = ("other", "profile_tab")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Записать изменения (без флага — только показать)")
    parser.add_argument(
        "--fix-entity-keys",
        action="store_true",
        help=(
            "Дополнительно перерезолвить entity_key у profile-строк "
            "(хендл → user_id, как при записи)"
        ),
    )
    args = parser.parse_args()

    moves: Counter[tuple[str, str]] = Counter()
    touched_days: set[object] = set()
    changed = 0

    session_factory = get_session_factory()
    with session_factory() as db:
        types = list(RECOUNTABLE_TYPES) + (["profile"] if args.fix_entity_keys else [])
        query = select(PageViewEvent).where(PageViewEvent.page_type.in_(types))

        for event in db.execute(query).scalars():
            page_type, entity_key = classify_page(event.path)
            # Профили хранят в entity_key user_id, а не хендл из адреса —
            # повторяем то же, что делает record_page_view, иначе строка не
            # сойдётся с остальными в «топе профилей».
            is_self = event.is_self
            if page_type == "profile" and entity_key:
                owner = resolve_profile_handle(db, entity_key)
                if owner is not None:
                    entity_key = str(owner.id)
                    is_self = event.viewer_user_id is not None and owner.id == event.viewer_user_id
            if (
                page_type == event.page_type
                and entity_key == (event.entity_key or "")
                and is_self == event.is_self
            ):
                continue
            moves[(event.page_type, page_type)] += 1
            touched_days.add(event.ts.date())
            changed += 1
            if args.apply:
                event.page_type = page_type
                event.entity_key = entity_key
                event.is_self = is_self

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
