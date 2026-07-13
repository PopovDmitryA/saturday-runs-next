"""Backfill events.event_number / event_summaries.event_number для s95.

JSON API s95 не отдаёт номер забега, поэтому исторические события лежат с NULL.
Номер = хронологический ранг активности в списке /events/{slug}.json локации —
совпадает с колонкой «#» на странице локации сайта (проверено: Иваново #162,
Щёлково #115). Заодно приводит title к виду «{Локация} #N», как у 5 вёрст.

Ходит только в лёгкие list-эндпоинты (один запрос на локацию), протоколы не качает.
При 403/SSL-обрывах (антибот s95 на частые запросы) локация пропускается — скрипт
идемпотентен, недобранные локации добираются повторным запуском.

Запуск:
    docker compose exec api python scripts/backfill_s95_event_numbers.py [--dry-run] [--sleep 10]
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "/app")

from app.db.session import get_session_factory  # noqa: E402
from app.models import Event, EventSummary, Location, Platform  # noqa: E402
from app.s95.api_client import fetch_all_locations, fetch_event_activities  # noqa: E402
from app.sync.s95_global_sync_api import event_numbers_by_date  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="показать изменения без записи")
    parser.add_argument(
        "--sleep", type=float, default=10.0,
        help="пауза между локациями, сек (щадящий темп против антибота s95)",
    )
    args = parser.parse_args()

    db = get_session_factory()()
    platform = db.query(Platform).filter(Platform.code == "s95").one()

    api_locations = fetch_all_locations()
    print(f"Локаций в API: {len(api_locations)}")

    total_events = 0
    total_summaries = 0
    missing_locations: list[str] = []
    failed_locations: list[str] = []

    for api_loc in api_locations:
        location = (
            db.query(Location)
            .filter(Location.platform_id == platform.id, Location.external_key == api_loc.slug)
            .one_or_none()
        )
        if location is None:
            missing_locations.append(api_loc.slug)
            continue

        try:
            refs = fetch_event_activities(f"{api_loc.domain}/events/{api_loc.slug}.json")
        except Exception as exc:
            print(f"  {api_loc.slug}: список активностей не получен: {exc}")
            failed_locations.append(api_loc.slug)
            time.sleep(args.sleep)
            continue
        numbers = event_numbers_by_date(refs)

        loc_events = 0
        loc_summaries = 0
        for event in (
            db.query(Event)
            .filter(Event.platform_id == platform.id, Event.location_id == location.id)
            .all()
        ):
            number = numbers.get(event.event_date.isoformat())
            if number is None or event.event_number == number:
                continue
            event.event_number = number
            if not event.is_test_event:
                event.title = f"{location.name} #{number}"
            loc_events += 1

        for summary in (
            db.query(EventSummary)
            .filter(EventSummary.platform_id == platform.id, EventSummary.location_id == location.id)
            .all()
        ):
            number = numbers.get(summary.event_date.isoformat())
            if number is None or summary.event_number == number:
                continue
            summary.event_number = number
            loc_summaries += 1

        if loc_events or loc_summaries:
            print(f"  {api_loc.slug}: events +{loc_events}, summaries +{loc_summaries}")
        total_events += loc_events
        total_summaries += loc_summaries

        if args.dry_run:
            db.rollback()
        else:
            db.commit()
        time.sleep(args.sleep)

    if missing_locations:
        print(f"Локации из API без записи в БД (пропущены): {', '.join(missing_locations)}")
    if failed_locations:
        print(
            f"Не получены списки ({len(failed_locations)}): {', '.join(failed_locations)} — "
            "запустить скрипт ещё раз, уже обработанные локации он пропустит без записи"
        )
    mode = "DRY-RUN, ничего не записано" if args.dry_run else "записано"
    print(f"Итого ({mode}): events {total_events}, summaries {total_summaries}")


if __name__ == "__main__":
    main()
