#!/usr/bin/env python3
"""Перевести ключи 5 вёрст, выданные синком профиля, в общий формат.

До 13.09.2026 профиль лепил «участник:дата:площадка», а протокол —
«площадка:дата:участник»; волонтёрства расходились так же. Теперь формат один
(app/platform_adapters/five_verst/result_keys.py), но строки, записанные
профилем раньше, остались на старом ключе: 4093 пробежки и 3 волонтёрства на
копии от 30.08.2026.

Само по себе это чинится и без скрипта — у пробежек апсерт находит строку
запасным поиском по (событие, участник) и переписывает ключ при ближайшей
перекачке протокола. Но у волонтёрств такого запасного поиска нет: там строка
доживёт до перекачки протокола отдельной записью, а это лишний волонтёрский
день в счётчиках. Скрипт убирает это ожидание.

Идемпотентен: строки в целевом формате не трогает, при конфликте ключа на том
же событии оставляет как есть (её подчистит перезапись протокола).

Запуск:
    docker compose exec api python scripts/backfill_five_verst_profile_result_keys.py --dry-run
    make prod-run ARGS="scripts/backfill_five_verst_profile_result_keys.py"
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Event, Location, Platform, RunResult, VolunteerResult

# Профильный ключ пробежки: «участник:дата:площадка». Участник у 5 вёрст —
# всегда число, поэтому первый сегмент из цифр надёжно отличает старый формат
# от нового (slug площадки числом целиком не бывает).
_RUN_OLD = re.compile(r"^(\d+):(\d{4}-\d{2}-\d{2}):(.+)$")
# Профильное волонтёрство: «участник:дата:площадка:роль».
_VOL_OLD = re.compile(r"^(\d+):(\d{4}-\d{2}-\d{2}):(.+):([^:]+)$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Не писать в БД, только посчитать.")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        totals = {"runs": 0, "vols": 0, "conflicts": 0}

        for model, pattern, bucket in (
            (RunResult, _RUN_OLD, "runs"),
            (VolunteerResult, _VOL_OLD, "vols"),
        ):
            rows = (
                db.query(model)
                .join(Event, Event.id == model.event_id)
                .join(Location, Location.id == Event.location_id)
                .join(Platform, Platform.id == Location.platform_id)
                .filter(Platform.code == "five_verst")
                .all()
            )
            for row in rows:
                match = pattern.match(row.external_result_key or "")
                if match is None:
                    continue
                if bucket == "runs":
                    user, day, place = match.groups()
                    new_key = f"{place}:{day}:{user}"
                else:
                    user, day, place, role = match.groups()
                    new_key = f"{place}:{day}:vol:{user}:{role}"
                if new_key == row.external_result_key:
                    continue
                taken = (
                    db.query(model.id)
                    .filter(
                        model.event_id == row.event_id,
                        model.external_result_key == new_key,
                        model.id != row.id,
                    )
                    .first()
                )
                if taken is not None:
                    # Тот же финиш уже лежит под новым ключом — дубль снимет
                    # ближайшая перезапись протокола, она авторитетна.
                    totals["conflicts"] += 1
                    continue
                print(f"  {row.external_result_key} → {new_key}")
                if not args.dry_run:
                    row.external_result_key = new_key
                totals[bucket] += 1

        if not args.dry_run:
            db.commit()

        verb = "Переписал бы" if args.dry_run else "Переписал"
        print(
            f"\n{verb}: пробежек {totals['runs']}, волонтёрств {totals['vols']};"
            f" пропущено из-за занятого ключа {totals['conflicts']}"
        )
        if args.dry_run:
            print("Запустите без --dry-run, чтобы применить.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
