#!/usr/bin/env python3
"""Разметка накопленных событий краулеров в page_view_events и пересчёт агрегатов.

Фильтр по user-agent отсекает роботов на входе, но события, записанные до него,
уже лежат в базе, а user-agent мы никогда не хранили. Опознать их задним числом
можно только по поведению, и у краулера оно очень характерное: localStorage
между страницами он не держит, поэтому каждый его заход — новый visitor_key
ровно с одним событием за всю жизнь.

Правило (проверено на проде 31.08.2026):

    анонимный visitor_key ("a:...")
    И ровно одно событие за всю историю этого visitor_key
    И page_type из списка обходимых разделов (по умолчанию location_protocol)

Почему именно так: на 15-23.08, до того как краулер Meta нашёл страницы
протоколов, правило даёт РОВНО НОЛЬ совпадений — то есть живых людей оно не
задевает. С 24.08 оно начинает ловить обход: 227 событий 26.08, 2111 за 30.08.
Правило намеренно узкое — часть событий бота (заходы в другие разделы) оно
пропускает, но лучше недосчитать робота, чем стереть живого человека.

Разметка обратима: события не удаляются, ставится флаг is_bot, снять его можно
`--unmark`. Дни, которых коснулась разметка, пересобираются в page_stats_daily.

    python scripts/mark_bot_page_events.py                  # показать, что будет
    python scripts/mark_bot_page_events.py --apply          # разметить и пересчитать
    python scripts/mark_bot_page_events.py --unmark --apply # откатить разметку
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.services.page_analytics_service import rollup_day  # noqa: E402

DEFAULT_PAGE_TYPES = ("location_protocol",)

CANDIDATES_SQL = """
WITH single_visit AS (
    SELECT visitor_key
    FROM page_view_events
    WHERE visitor_key LIKE 'a:%%'
    GROUP BY visitor_key
    HAVING count(*) = 1
)
SELECT p.id, (p.ts AT TIME ZONE 'Europe/Moscow')::date AS day
FROM page_view_events p
JOIN single_visit s ON s.visitor_key = p.visitor_key
WHERE p.page_type = ANY(:page_types)
  AND p.is_bot IS FALSE
"""

MARKED_SQL = """
SELECT id, (ts AT TIME ZONE 'Europe/Moscow')::date AS day
FROM page_view_events
WHERE is_bot IS TRUE
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="записать изменения (по умолчанию — сухой прогон)")
    parser.add_argument("--unmark", action="store_true", help="снять разметку вместо простановки")
    parser.add_argument(
        "--page-types",
        default=",".join(DEFAULT_PAGE_TYPES),
        help=f"разделы через запятую (по умолчанию {','.join(DEFAULT_PAGE_TYPES)})",
    )
    args = parser.parse_args()

    page_types = [value.strip() for value in args.page_types.split(",") if value.strip()]
    db = get_session_factory()()
    try:
        if args.unmark:
            rows = db.execute(text(MARKED_SQL)).all()
            action = "снять метку"
        else:
            rows = db.execute(text(CANDIDATES_SQL), {"page_types": page_types}).all()
            action = "пометить как бота"

        if not rows:
            print("Нечего делать: подходящих событий нет.")
            return 0

        by_day: Counter[date] = Counter(row.day for row in rows)
        print(f"Событий {action}: {len(rows)}")
        print("По дням (МСК):")
        for day in sorted(by_day):
            print(f"  {day}: {by_day[day]}")

        if not args.apply:
            print()
            print("Сухой прогон. Повторите с --apply, чтобы записать и пересобрать агрегаты.")
            return 0

        ids = [row.id for row in rows]
        new_value = not args.unmark
        # Пишем порциями: разметка разовая, но событий могут быть десятки тысяч,
        # а один IN на всё это — лишняя нагрузка на планировщик.
        for offset in range(0, len(ids), 5000):
            chunk = ids[offset : offset + 5000]
            db.execute(
                text("UPDATE page_view_events SET is_bot = :value WHERE id = ANY(:ids)"),
                {"value": new_value, "ids": chunk},
            )
        db.commit()
        print(f"Обновлено строк: {len(ids)}")

        print("Пересборка дневных агрегатов:")
        for day in sorted(by_day):
            groups = rollup_day(db, day)
            print(f"  {day}: строк агрегата {groups}")
        print("Готово.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
