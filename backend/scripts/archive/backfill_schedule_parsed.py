"""Разовый бэкфилл schedule_parsed по уже собранным описаниям локаций.

Синк реестра пересчитывает schedule_parsed только когда текст описания
меняется — существующие строки без бэкфилла ждали бы своего изменения годами.
Повторный запуск безопасен (перезаписывает тем же результатом).

Запуск: python scripts/backfill_schedule_parsed.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import get_session_factory  # noqa: E402
from app.models import LocationDescription  # noqa: E402
from app.services.location_schedule_service import parse_schedule_text  # noqa: E402


def main() -> None:
    db = get_session_factory()()
    rows = db.query(LocationDescription).filter(LocationDescription.schedule_text.isnot(None)).all()
    parsed_count = 0
    empty_count = 0
    for row in rows:
        parsed = parse_schedule_text(row.schedule_text)
        row.schedule_parsed = parsed
        if parsed:
            parsed_count += 1
        else:
            empty_count += 1
    db.commit()
    print(f"Описаний с расписанием: {parsed_count}, не распарсилось: {empty_count} из {len(rows)}")


if __name__ == "__main__":
    main()
